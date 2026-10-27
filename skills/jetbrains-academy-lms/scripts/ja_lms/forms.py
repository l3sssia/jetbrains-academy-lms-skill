"""Minimal HTML form handling for the LMS's server-rendered Django forms.

The add/edit assignment pages are full-replace forms: a POST overwrites every
field, so a partial POST would blank whatever it omits. ``parse_form`` extracts
exactly what a browser would submit, so callers can change a few fields and
send the rest back unchanged.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Dict, List, Optional, Tuple

@dataclass
class SelectInfo:
    multiple: bool
    options: List[Tuple[str, str]] = field(default_factory=list)  # (value, label)


@dataclass
class HTMLForm:
    action: Optional[str]
    method: str
    enctype: str
    # Ordered successful controls as a browser would submit them.
    fields: Dict[str, object] = field(default_factory=dict)
    file_fields: List[str] = field(default_factory=list)
    checkboxes: Dict[str, str] = field(default_factory=dict)  # name -> value when checked
    selects: Dict[str, SelectInfo] = field(default_factory=dict)
    submit: Optional[Tuple[str, str]] = None

    def payload(self) -> List[Tuple[str, str]]:
        """Flatten fields into submission order, multi-selects repeated."""
        items: List[Tuple[str, str]] = []
        for name, value in self.fields.items():
            if isinstance(value, list):
                items.extend((name, v) for v in value)
            elif value is not None:
                items.append((name, value))
        if self.submit:
            items.append(self.submit)
        return items


class _FormParser(HTMLParser):
    def __init__(self, form_index: int) -> None:
        super().__init__(convert_charrefs=True)
        self.form_index = form_index
        self.forms_seen = -1
        self.active = False
        self.done = False
        self.form: Optional[HTMLForm] = None
        self._select: Optional[str] = None
        self._select_selected: List[str] = []
        self._select_first: Optional[str] = None
        self._option: Optional[dict] = None
        self._option_text: List[str] = []
        self._textarea: Optional[str] = None
        self._textarea_text: List[str] = []

    def handle_starttag(self, tag, attrs):
        a = {k: (v if v is not None else "") for k, v in attrs}
        if tag == "form" and not self.done and not self.active:
            self.forms_seen += 1
            if self.forms_seen == self.form_index:
                self.active = True
                self.form = HTMLForm(
                    action=a.get("action") or None,
                    method=(a.get("method") or "get").lower(),
                    enctype=a.get("enctype") or "application/x-www-form-urlencoded",
                )
            return
        if not self.active:
            return
        name = a.get("name")
        if tag == "input" and name and "disabled" not in a:
            kind = (a.get("type") or "text").lower()
            if kind in ("button", "reset", "image"):
                return
            if kind == "submit":
                if self.form.submit is None:
                    self.form.submit = (name, a.get("value", ""))
                return
            if kind == "file":
                self.form.file_fields.append(name)
                return
            if kind in ("checkbox", "radio"):
                value = a.get("value") or "on"
                if kind == "checkbox":
                    self.form.checkboxes[name] = value
                if "checked" in a:
                    self.form.fields[name] = value
                elif kind == "checkbox":
                    self.form.fields.setdefault(name, None)
                return
            self.form.fields[name] = a.get("value", "")
        elif tag == "select" and name and "disabled" not in a:
            self._select = name
            self._select_selected = []
            self._select_first = None
            self.form.selects[name] = SelectInfo(multiple="multiple" in a)
        elif tag == "option" and self._select:
            self._option = a
            self._option_text = []
        elif tag == "textarea" and name and "disabled" not in a:
            self._textarea = name
            self._textarea_text = []

    def handle_data(self, data):
        if self._option is not None:
            self._option_text.append(data)
        if self._textarea is not None:
            self._textarea_text.append(data)

    def _close_option(self):
        if self._option is None:
            return
        label = " ".join("".join(self._option_text).split())
        value = self._option.get("value", label)
        self.form.selects[self._select].options.append((value, label))
        if self._select_first is None:
            self._select_first = value
        if "selected" in self._option:
            self._select_selected.append(value)
        self._option = None

    def handle_endtag(self, tag):
        if not self.active:
            return
        if tag == "option":
            self._close_option()
        elif tag == "select" and self._select:
            self._close_option()
            info = self.form.selects[self._select]
            if info.multiple:
                self.form.fields[self._select] = list(self._select_selected)
            else:
                chosen = self._select_selected[-1] if self._select_selected else self._select_first
                self.form.fields[self._select] = chosen if chosen is not None else ""
            self._select = None
        elif tag == "textarea" and self._textarea:
            text = "".join(self._textarea_text)
            # Browsers drop a single leading newline inside <textarea>.
            if text.startswith("\r\n"):
                text = text[2:]
            elif text.startswith("\n"):
                text = text[1:]
            self.form.fields[self._textarea] = text
            self._textarea = None
        elif tag == "form":
            self.active = False
            self.done = True


def parse_form(html: str, *, has_field: Optional[str] = None, index: int = 0) -> HTMLForm:
    """Parse one form from ``html``.

    With ``has_field``, picks the first form containing a control of that name;
    otherwise the ``index``-th form on the page.
    """
    if has_field:
        i = 0
        while True:
            parser = _FormParser(i)
            parser.feed(html)
            if parser.form is None:
                raise ValueError(f"no form with field {has_field!r} found")
            f = parser.form
            if has_field in f.fields or has_field in f.selects or has_field in f.file_fields:
                return f
            i += 1
    parser = _FormParser(index)
    parser.feed(html)
    if parser.form is None:
        raise ValueError("form not found")
    return parser.form


_ERROR_BLOCK = re.compile(
    r'<(?P<tag>ul|div|span|p|strong)[^>]*class="[^"]*(?:errorlist|invalid-feedback|alert-danger|error)[^"]*"[^>]*>(?P<body>.*?)</(?P=tag)>',
    re.S | re.I,
)
_TAGS = re.compile(r"<[^>]+>")


def extract_form_errors(html: str) -> List[str]:
    """Best-effort extraction of Django/crispy validation messages."""
    errors = []
    for m in _ERROR_BLOCK.finditer(html):
        text = " ".join(_TAGS.sub(" ", m.group("body")).split())
        if text and text not in errors:
            errors.append(text)
    return errors


def encode_multipart(
    items: List[Tuple[str, str]], file_fields: List[str] = ()
) -> Tuple[bytes, str]:
    """Encode text fields plus empty file parts, as a browser does for an
    untouched ``<input type=file>``."""
    boundary = "----ja-lms-" + uuid.uuid4().hex
    out = []
    for name, value in items:
        out.append(f"--{boundary}\r\n".encode())
        out.append(f'Content-Disposition: form-data; name="{_quote(name)}"\r\n\r\n'.encode())
        out.append(str(value).encode("utf-8"))
        out.append(b"\r\n")
    for name in file_fields:
        out.append(f"--{boundary}\r\n".encode())
        out.append(
            f'Content-Disposition: form-data; name="{_quote(name)}"; filename=""\r\n'
            "Content-Type: application/octet-stream\r\n\r\n\r\n".encode()
        )
    out.append(f"--{boundary}--\r\n".encode())
    return b"".join(out), f"multipart/form-data; boundary={boundary}"


def _quote(name: str) -> str:
    return name.replace("\\", "\\\\").replace('"', "%22").replace("\r", "%0D").replace("\n", "%0A")
