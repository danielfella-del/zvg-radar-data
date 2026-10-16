#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from hh_mv_notices import collect_issues, issue_key, session
from official_notice_common import clean

INDEX = 'https://www.mecklenburg-vorpommern.de/landesregierung/jm/amtsblaetter'


def discover_pdfs(html, base_url=INDEX):
    """Read both visible links and the site's embedded 'Mehr anzeigen' data."""
    soup = BeautifulSoup(html, 'html.parser')
    links = []

    def add(url, label):
        url = urljoin(base_url, url)
        if ('amtlicher anzeiger' in clean(label.replace('_', ' ')).lower()
                and urlparse(url).hostname in {'www.mecklenburg-vorpommern.de', 'mecklenburg-vorpommern.de'}
                and issue_key('mv', url) and url not in links):
            links.append(url)

    for link in soup.find_all('a', href=True):
        add(link['href'], link.get_text(' ', strip=True))
    visible_count = len(links)

    def walk(value):
        if isinstance(value, dict):
            document = value.get('field_media_document')
            if isinstance(document, dict):
                add((document.get('uri') or {}).get('url', ''), value.get('name', ''))
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    # Parse JSON only; never execute JavaScript from the source page.
    parts = []
    for script in soup.find_all('script'):
        match = re.fullmatch(r'self\.__next_f\.push\((.*)\)\s*;?', script.get_text(), re.S)
        if not match:
            continue
        try:
            payload = json.loads(match[1])
        except ValueError:
            continue
        if isinstance(payload, list) and len(payload) > 1 and isinstance(payload[1], str):
            parts.append(payload[1])
    for line in ''.join(parts).splitlines():
        try:
            walk(json.loads(line.split(':', 1)[1]))
        except (ValueError, IndexError):
            continue
    if soup.select_one('button.link-list__show-more') and len(links) <= visible_count:
        raise RuntimeError('MV: ausgeblendete Ausgaben konnten nicht vollständig gelesen werden')
    return links


def download_pdf(url):
    with session() as client:
        response = client.get(url, timeout=(10, 30))
        response.raise_for_status()
        return response.url, response.content


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', default='data/supplemental_mv.json')
    parser.add_argument('--max-issues', type=int, default=52)
    args = parser.parse_args()
    with session() as client:
        response = client.get(INDEX, timeout=(10, 30))
        response.raise_for_status()
        links = discover_pdfs(response.text, response.url)
    if not links:
        raise RuntimeError('MV: keine amtlichen Anzeiger gefunden; bisherigen Datenbestand behalten')
    collect_issues('mv', INDEX, links[:args.max_issues], download_pdf, args.output)


if __name__ == '__main__':
    main()

