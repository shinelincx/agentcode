"""Static validation for the two files published to G2."""
from collections import Counter
from html.parser import HTMLParser
import re


_URL_ATTRS = {"action", "background", "cite", "data", "formaction", "href", "longdesc",
              "manifest", "ping", "poster", "src", "srcset", "xlink:href"}
_DATA_IMAGE = re.compile(r"data:image/(?:png|jpeg|gif|webp);base64,[a-z0-9+/=]+", re.I)
_CSS_URL = re.compile(r"\burl\(\s*(['\"]?)(.*?)\1\s*\)", re.I | re.S)
_AC_ROW = re.compile(r"(?:-\s+)?(AC-[1-9]\d*)\s*→\s*(.+)")


def _local_ref(value: str) -> bool:
    return value.startswith("#") or bool(_DATA_IMAGE.fullmatch(value))


class PrototypeHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags: set[str] = set()
        self.ids: Counter = Counter()
        self.errors: list[str] = []
        self._style = False
        self._body = False
        self._nonvisual = 0
        self.visible_body = False

    def _check_css(self, value: str) -> None:
        if re.search(r"@import\b", value, re.I) or any(not _local_ref(m[2].strip()) for m in _CSS_URL.finditer(value)):
            self.errors.append("样式须内联，资源须嵌入文件")

    def handle_starttag(self, tag, attrs):
        self.tags.add(tag)
        attrs = dict(attrs)
        if tag == "body":
            self._body = True
        if tag in {"script", "style", "template"}:
            self._nonvisual += 1
        if tag == "style":
            self._style = True
        if self._body and not self._nonvisual:
            if attrs.get("id"):
                self.ids[attrs["id"]] += 1
            if tag in {"button", "canvas", "img", "input", "select", "svg", "textarea"}:
                self.visible_body = True
        if tag in {"base", "embed", "iframe", "link", "object"} or (
                tag == "meta" and (attrs.get("http-equiv") or "").lower() == "refresh"):
            self.errors.append(f"自包含原型不使用 <{tag}>")
        for key in _URL_ATTRS & attrs.keys():
            value = (attrs[key] or "").strip()
            if (key == "href" and value in ("", "#")) or (key != "srcset" and _local_ref(value)):
                continue
            self.errors.append(f"<{tag}> 的 {key} 须为页内引用或内嵌图片")
        self._check_css(attrs.get("style") or "")

    def handle_endtag(self, tag):
        if tag == "body":
            self._body = False
        if tag == "style":
            self._style = False
        if tag in {"script", "style", "template"}:
            self._nonvisual = max(0, self._nonvisual - 1)

    def handle_data(self, text):
        if self._style:
            self._check_css(text)
        elif self._body and not self._nonvisual and text.strip():
            self.visible_body = True


def mapping_errors(mapping: str, wanted: list[str], anchors: Counter) -> list[str]:
    errors: list[str] = []
    seen = set()
    for line in filter(str.strip, mapping.splitlines()):
        match = _AC_ROW.fullmatch(line.strip())
        if not match:
            errors.append("每条验收映射须包含 AC、类别及验证依据")
            continue
        ac, details = match.groups()
        if ac in seen or ac not in wanted:
            errors.append(f"验收映射 AC 重复或未知：{ac}")
        seen.add(ac)
        columns = [part.strip() for part in details.split("|")]
        match columns:
            case ["原型演示", anchor, action, expected] if action and expected:
                if not re.fullmatch(r"#[A-Za-z_][A-Za-z0-9_-]*", anchor) or anchors[anchor[1:]] != 1:
                    errors.append(f"{ac} 的页面锚点须唯一存在：{anchor}")
            case ["后续验证", method] if method:
                pass
            case _:
                errors.append(f"{ac} 的验收映射须提供定位、操作与预期，或后续验证方式")
    if missing := [ac for ac in wanted if ac not in seen]:
        errors.append(f"原型验收映射缺少：{', '.join(missing)}")
    return errors
