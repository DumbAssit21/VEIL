"""Opt-in, read-only update notifications. Never downloads or executes software."""
from __future__ import annotations
import json
import re
from urllib.request import Request, urlopen
from packaging.version import Version
from .version import __version__, GITHUB_REPOSITORY, GITHUB_RELEASES_URL

API = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases?per_page=20"
TAG_PATTERN = re.compile(r"v\d+\.\d+\.\d+(?:-(?:alpha|beta|rc)\.\d+)?$")

def find_update(fetch=None) -> dict | None:
    """Return newer public GitHub release metadata, never an arbitrary download URL.

    The caller must opt in or explicitly request this query. No vault paths,
    files, credentials, or keys are transmitted; GitHub sees the network request.
    """
    if fetch is None:
        def fetch():
            req=Request(API, headers={'Accept':'application/vnd.github+json',
                'User-Agent':f'VEIL/{__version__}', 'X-GitHub-Api-Version':'2022-11-28'})
            with urlopen(req,timeout=7) as response:
                if response.status!=200:
                    raise RuntimeError('GitHub could not be reached.')
                data=response.read(512 * 1024 + 1)
            if len(data)>512*1024:
                raise RuntimeError('The release response was unexpectedly large.')
            return json.loads(data)
    releases=fetch()
    if not isinstance(releases,list):
        raise RuntimeError('GitHub returned an unexpected response.')
    current=Version(__version__)
    candidates=[]
    for obj in releases:
        if not isinstance(obj,dict) or obj.get('draft'):
            continue
        tag=obj.get('tag_name','')
        if not isinstance(tag,str) or not TAG_PATTERN.fullmatch(tag):
            continue
        version=Version(tag[1:])
        if version>current:
            candidates.append((version,tag))
    if not candidates:
        return None
    _version,tag=max(candidates)
    return {'tag':tag, 'releases_url':GITHUB_RELEASES_URL}
