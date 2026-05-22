from __future__ import annotations

from dataclasses import asdict, dataclass
from urllib.parse import urljoin

from bs4 import BeautifulSoup


@dataclass(frozen=True)
class DetailLink:
  kind: str
  text: str
  url: str
  row_index: int | None = None
  course_id: str | None = None
  section_id: str | None = None

  def to_dict(self) -> dict[str, str | int | None]:
    return asdict(self)


def collect_detail_links(html: str, base_url: str) -> list[DetailLink]:
  soup = BeautifulSoup(html, 'lxml')
  links: list[DetailLink] = []

  for row_index, row in enumerate(soup.select('tr')):
    cells = row.find_all(['td', 'th'])
    course_id = _cell_text(cells, 1)
    section_id = _cell_text(cells, 2)
    for anchor in row.find_all('a', href=True):
      href = str(anchor.get('href', ''))
      kind = _classify_detail_href(href)
      if kind is None:
        continue
      links.append(
        DetailLink(
          kind=kind,
          text=anchor.get_text(' ', strip=True),
          url=urljoin(base_url, href),
          row_index=row_index,
          course_id=course_id,
          section_id=section_id,
        )
      )

  return _dedupe_links(links)


def _classify_detail_href(href: str) -> str | None:
  if 'js.vjsKcbBs.do' in href and 'm=showToXs' in href:
    return 'course'
  if 'xkBks.vxkBksJxjhBs.do' in href and 'm=showJsDetail' in href:
    return 'teacher'
  if 'xk.xk_syrwb.do' in href and 'm=show' in href:
    return 'experiment'
  return None


def _cell_text(cells: list, index: int) -> str | None:
  if len(cells) <= index:
    return None
  text = cells[index].get_text(' ', strip=True)
  return text or None


def _dedupe_links(links: list[DetailLink]) -> list[DetailLink]:
  seen: set[tuple[str, str]] = set()
  deduped: list[DetailLink] = []
  for link in links:
    key = (link.kind, link.url)
    if key in seen:
      continue
    seen.add(key)
    deduped.append(link)
  return deduped
