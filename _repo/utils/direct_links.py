# Copyright @juktijol
# Channel t.me/juktijol
#
# utils/direct_links.py — Direct Link Generator
#
# Resolves indirect links from 40+ file hosting sites to direct download URLs.
# Adapted from: https://github.com/anasty17/mirror-leech-telegram-bot
#
# Supported sites:
#   MediaFire (files + folders), GoFile, TeraBox, Pixeldrain, 1Fichier,
#   StreamTape, WeTransfer, SwissTransfer, qiwi.gg, mp4upload, berkasdrive,
#   BuzzHeavier, Send.cm, LinkBox, Doodstream family, Racaty, Krakenfiles,
#   Solidfiles, Uploadee, TmpSend, EasyUpload, StreamVid, StreamHub,
#   pCloud, AkmFiles, Shrdsk, FileLions+StreamWish, Hxfile, OneDrive,
#   GitHub releases, OSDN, Yandex Disk, devuploads, UploadHaven,
#   FuckingFast, Lulacloud, MediaFile, BerkasDrive, SwisstTransfer

import re
from hashlib import sha256
from http.cookiejar import MozillaCookieJar
from json import loads
from os import path as ospath
from re import findall, match, search
from time import sleep
from urllib.parse import parse_qs, urlparse, quote
from uuid import uuid4
from base64 import b64decode, b64encode

import requests
from requests import Session
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from utils import LOGGER


# ─────────────────────────────────────────────────────────────────────────────
# EXCEPTIONS
# ─────────────────────────────────────────────────────────────────────────────

class DirectLinkException(Exception):
    """Raised when a direct link cannot be generated."""
    pass


# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0"
)

PASSWORD_ERROR = (
    "This link requires a password!\n"
    "Add password after `::` → `link::my_password`"
)


# ─────────────────────────────────────────────────────────────────────────────
# ROUTER — main entry point
# ─────────────────────────────────────────────────────────────────────────────

def generate_direct_link(url: str) -> str | dict:
    """
    Detect the hosting site from the URL and return a direct download link.

    Returns:
        str  — a single direct download URL
        dict — folder/multi-file info: {"title", "total_size", "contents": [...]}
              where each content item is {"filename", "path", "url"}
        May also return a tuple (url, headers_list) for sites requiring custom headers.

    Raises:
        DirectLinkException — if the link cannot be resolved.
    """
    url = url.strip()
    domain = urlparse(url).hostname or ""

    # Route by domain
    if not domain:
        raise DirectLinkException("Invalid URL — could not parse domain.")

    # ── Yandex Disk ───────────────────────────────────────────────────────────
    if "yadi.sk" in url or "disk.yandex." in url:
        return _yandex_disk(url)

    # ── BuzzHeavier ───────────────────────────────────────────────────────────
    if "buzzheavier.com" in domain:
        return _buzzheavier(url)

    # ── devuploads ────────────────────────────────────────────────────────────
    if "devuploads" in domain:
        return _devuploads(url)

    # ── Lulacloud ─────────────────────────────────────────────────────────────
    if "lulacloud.com" in domain:
        return _lulacloud(url)

    # ── UploadHaven ───────────────────────────────────────────────────────────
    if "uploadhaven" in domain:
        return _uploadhaven(url)

    # ── FuckingFast ───────────────────────────────────────────────────────────
    if "fuckingfast.co" in domain:
        return _fuckingfast(url)

    # ── MediaFile ─────────────────────────────────────────────────────────────
    if "mediafile.cc" in domain:
        return _mediafile(url)

    # ── MediaFire ─────────────────────────────────────────────────────────────
    if "mediafire.com" in domain:
        return _mediafire(url)

    # ── OSDN ──────────────────────────────────────────────────────────────────
    if "osdn.net" in domain:
        return _osdn(url)

    # ── GitHub Releases ───────────────────────────────────────────────────────
    if "github.com" in domain:
        return _github(url)

    # ── Transfer.it ───────────────────────────────────────────────────────────
    if "transfer.it" in domain:
        return _transfer_it(url)

    # ── HxFile ────────────────────────────────────────────────────────────────
    if "hxfile.co" in domain:
        return _hxfile(url)

    # ── OneDrive ──────────────────────────────────────────────────────────────
    if "1drv.ms" in domain:
        return _onedrive(url)

    # ── Pixeldrain ────────────────────────────────────────────────────────────
    if any(x in domain for x in ["pixeldrain.com", "pixeldra.in"]):
        return _pixeldrain(url)

    # ── Racaty ────────────────────────────────────────────────────────────────
    if "racaty" in domain:
        return _racaty(url)

    # ── 1Fichier ──────────────────────────────────────────────────────────────
    if "1fichier.com" in domain:
        return _fichier(url)

    # ── Solidfiles ────────────────────────────────────────────────────────────
    if "solidfiles.com" in domain:
        return _solidfiles(url)

    # ── KrakenFiles ───────────────────────────────────────────────────────────
    if "krakenfiles.com" in domain:
        return _krakenfiles(url)

    # ── Upload.ee ─────────────────────────────────────────────────────────────
    if "upload.ee" in domain:
        return _uploadee(url)

    # ── GoFile ────────────────────────────────────────────────────────────────
    if "gofile.io" in domain:
        return _gofile(url)

    # ── Send.cm ───────────────────────────────────────────────────────────────
    if "send.cm" in domain:
        return _send_cm(url)

    # ── TmpSend ───────────────────────────────────────────────────────────────
    if "tmpsend.com" in domain:
        return _tmpsend(url)

    # ── EasyUpload ────────────────────────────────────────────────────────────
    if "easyupload.io" in domain:
        return _easyupload(url)

    # ── StreamVid ─────────────────────────────────────────────────────────────
    if "streamvid.net" in domain:
        return _streamvid(url)

    # ── StreamHub ─────────────────────────────────────────────────────────────
    if any(x in domain for x in ["streamhub.ink", "streamhub.to"]):
        return _streamhub(url)

    # ── pCloud ────────────────────────────────────────────────────────────────
    if "u.pcloud.link" in domain:
        return _pcloud(url)

    # ── qiwi.gg ───────────────────────────────────────────────────────────────
    if "qiwi.gg" in domain:
        return _qiwi(url)

    # ── mp4upload ─────────────────────────────────────────────────────────────
    if "mp4upload.com" in domain:
        return _mp4upload(url)

    # ── BerkasDrive ───────────────────────────────────────────────────────────
    if "berkasdrive.com" in domain:
        return _berkasdrive(url)

    # ── SwissTransfer ─────────────────────────────────────────────────────────
    if "swisstransfer.com" in domain:
        return _swisstransfer(url)

    # ── AkmFiles ──────────────────────────────────────────────────────────────
    if any(x in domain for x in ["akmfiles.com", "akmfls.xyz"]):
        return _akmfiles(url)

    # ── Doodstream family ─────────────────────────────────────────────────────
    _dood_domains = {
        "dood.watch", "doodstream.com", "dood.to", "dood.so", "dood.cx",
        "dood.la", "dood.ws", "dood.sh", "doodstream.co", "dood.pm",
        "dood.wf", "dood.re", "dood.video", "dooood.com", "dood.yt",
        "doods.yt", "dood.stream", "doods.pro", "ds2play.com", "d0o0d.com",
        "ds2video.com", "do0od.com", "d000d.com",
    }
    if any(x in domain for x in _dood_domains):
        return _doods(url)

    # ── StreamTape family ─────────────────────────────────────────────────────
    _tape_domains = {
        "streamtape.com", "streamtape.co", "streamtape.cc", "streamtape.to",
        "streamtape.net", "streamta.pe", "streamtape.xyz",
    }
    if any(x in domain for x in _tape_domains):
        return _streamtape(url)

    # ── WeTransfer ────────────────────────────────────────────────────────────
    if any(x in domain for x in ["wetransfer.com", "we.tl"]):
        return _wetransfer(url)

    # ── TeraBox family ────────────────────────────────────────────────────────
    _tera_domains = {
        "terabox.com", "nephobox.com", "4funbox.com", "mirrobox.com",
        "momerybox.com", "teraboxapp.com", "1024tera.com", "terabox.app",
        "gibibox.com", "goaibox.com", "terasharelink.com", "teraboxlink.com",
        "freeterabox.com", "1024terabox.com", "teraboxshare.com",
        "terafileshare.com", "terabox.club",
    }
    if any(x in domain for x in _tera_domains):
        return _terabox(url)

    # ── FileLions / StreamWish family ─────────────────────────────────────────
    _lions_domains = {
        "filelions.co", "filelions.site", "filelions.live", "filelions.to",
        "mycloudz.cc", "cabecabean.lol", "filelions.online", "embedwish.com",
        "kitabmarkaz.xyz", "wishfast.top", "streamwish.to", "kissmovies.net",
    }
    if any(x in domain for x in _lions_domains):
        return _filelions_streamwish(url)

    # ── LinkBox ───────────────────────────────────────────────────────────────
    if any(x in domain for x in ["linkbox.to", "lbx.to", "teltobx.net", "telbx.net", "linkbox.cloud"]):
        return _linkbox(url)

    # ── Shrdsk ────────────────────────────────────────────────────────────────
    if "shrdsk.me" in domain:
        return _shrdsk(url)

    raise DirectLinkException(f"No direct link handler found for: {domain}")


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _session_with_retries() -> Session:
    """Create a requests Session with automatic retry logic."""
    s = Session()
    adapter = HTTPAdapter(max_retries=Retry(total=5, backoff_factor=0.3))
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    s.headers.update({"User-Agent": USER_AGENT})
    return s


def _get_captcha_token(session: Session, params: dict) -> str | None:
    """Fetch a reCAPTCHA v2 token for sites that require it."""
    api = "https://www.google.com/recaptcha/api2"
    try:
        from lxml.etree import HTML as lhtml
        res = session.get(f"{api}/anchor", params=params)
        tree = lhtml(res.text)
        anchor = tree.xpath('//input[@id="recaptcha-token"]/@value')
        if not anchor:
            return None
        params["c"] = anchor[0]
        params["reason"] = "q"
        res = session.post(f"{api}/reload", params=params)
        tokens = findall(r'"rresp","(.*?)"', res.text)
        return tokens[0] if tokens else None
    except Exception:
        return None


def _cloudscraper_session():
    """Create a cloudscraper session for Cloudflare-protected sites."""
    try:
        from cloudscraper import create_scraper
        return create_scraper()
    except ImportError:
        LOGGER.warning("[DirectLinks] cloudscraper not installed — some sites may fail.")
        return _session_with_retries()


# ─────────────────────────────────────────────────────────────────────────────
# SITE HANDLERS (alphabetical)
# ─────────────────────────────────────────────────────────────────────────────

def _akmfiles(url: str) -> str:
    with _cloudscraper_session() as s:
        html = s.post(url, data={"op": "download2", "id": url.split("/")[-1]}).text
    try:
        from lxml.etree import HTML as lhtml
        link = lhtml(html).xpath("//a[contains(@class,'btn btn-dow')]/@href")
    except ImportError:
        link = findall(r'<a[^>]+class="[^"]*btn btn-dow[^"]*"[^>]+href="([^"]+)"', html)
    if not link:
        raise DirectLinkException("AkmFiles: direct link not found.")
    return link[0]


def _berkasdrive(url: str) -> str:
    try:
        res = requests.get(url)
        m = search(r"showFileInformation\((.*?)\)", res.text)
        if not m:
            raise DirectLinkException("BerkasDrive: encoded link not found.")
        return b64decode(m.group(1).strip("()\"'")).decode("utf-8")
    except Exception as e:
        raise DirectLinkException(f"BerkasDrive: {e}")


def _buzzheavier(url: str) -> str | dict:
    pat = r"^https?://buzzheavier\.com/[a-zA-Z0-9]+$"
    if not match(pat, url):
        return url

    def _fetch(path: str, folder: bool = False) -> str | None:
        if "/download" not in path:
            path += "/download"
        s = _session_with_retries()
        s.headers.update({
            "referer": path.split("/download")[0],
            "hx-current-url": path.split("/download")[0],
            "hx-request": "true",
        })
        res = s.get(path)
        loc = res.headers.get("Hx-Redirect")
        if not loc and folder:
            return None
        if not loc:
            raise DirectLinkException("BuzzHeavier: no redirect found.")
        return loc

    with _session_with_retries() as s:
        try:
            from lxml.etree import HTML as lhtml
            tree = lhtml(s.get(url).text)
        except ImportError:
            raise DirectLinkException("BuzzHeavier: lxml required.")

        single = tree.xpath(
            "//a[contains(@class,'link-button') and contains(@class,'gay-button')]/@hx-get"
        )
        if single:
            return _fetch(f"https://buzzheavier.com{single[0]}")

        rows = tree.xpath("//tbody[@id='tbody']/tr")
        if not rows:
            raise DirectLinkException("BuzzHeavier: no download link found.")

        details = {"contents": [], "title": "", "total_size": 0}
        for row in rows:
            try:
                filename = row.xpath(".//a")[0].text.strip()
                href = row.xpath(".//a")[0].attrib.get("href", "")
                size_txt = row.xpath(".//td[@class='text-center']/text()")[0].strip()
                dl_url = _fetch(f"https://buzzheavier.com{href}", folder=True)
                if dl_url:
                    details["contents"].append({"path": "", "filename": filename, "url": dl_url})
                    for unit, factor in [("TB", 1e12), ("GB", 1e9), ("MB", 1e6), ("KB", 1024)]:
                        if unit in size_txt:
                            details["total_size"] += float(size_txt.replace(unit, "").strip()) * factor
                            break
            except Exception:
                continue
        if not details["contents"]:
            raise DirectLinkException("BuzzHeavier: folder is empty.")
        titles = tree.xpath("//span/text()")
        details["title"] = titles[0].strip() if titles else "buzzheavier_folder"
        return details


def _devuploads(url: str) -> str:
    with _session_with_retries() as s:
        try:
            from lxml.etree import HTML as lhtml
        except ImportError:
            raise DirectLinkException("devuploads: lxml required.")

        res = s.get(url)
        html = lhtml(res.text)
        inputs = html.xpath("//input[@name]")
        if not inputs:
            raise DirectLinkException("devuploads: no form inputs found.")

        data = {i.get("name"): i.get("value") for i in inputs}
        res2 = s.post("https://gujjukhabar.in/", data=data)
        html2 = lhtml(res2.text)
        data2 = {i.get("name"): i.get("value") for i in html2.xpath("//input[@name]")}

        ipp_res = s.get("https://du2.devuploads.com/dlhash.php", headers={
            "Origin": "https://gujjukhabar.in", "Referer": "https://gujjukhabar.in/"
        })
        if not ipp_res.text:
            raise DirectLinkException("devuploads: ipp value not found.")
        data2["ipp"] = ipp_res.text.strip()

        rand_res = s.post(
            "https://devuploads.com/token/token.php",
            data={"rand": data2.get("rand", ""), "msg": ""},
            headers={"Origin": "https://gujjukhabar.in", "Referer": "https://gujjukhabar.in/"},
        )
        data2["xd"] = rand_res.text.strip()

        final = s.post(url, data=data2)
        html3 = lhtml(final.text)
        links = html3.xpath("//input[@name='orilink']/@value")
        if not links:
            raise DirectLinkException("devuploads: final direct link not found.")
        return links[0]


def _doods(url: str) -> tuple[str, list]:
    if "/e/" in url:
        url = url.replace("/e/", "/d/")
    parsed = urlparse(url)
    with _cloudscraper_session() as s:
        try:
            from lxml.etree import HTML as lhtml
        except ImportError:
            raise DirectLinkException("Doodstream: lxml required.")
        html = lhtml(s.get(url).text)
        link = html.xpath("//div[@class='download-content']//a/@href")
        if not link:
            raise DirectLinkException("Doodstream: token link not found.")
        token_url = f"{parsed.scheme}://{parsed.hostname}{link[0]}"
        sleep(2)
        res2 = s.get(token_url)
        dl = search(r"window\.open\('(\S+)'", res2.text)
        if not dl:
            raise DirectLinkException("Doodstream: download link not found.")
    return dl.group(1), [f"Referer: {parsed.scheme}://{parsed.hostname}/"]


def _easyupload(url: str) -> str:
    pwd = ""
    if "::" in url:
        pwd = url.split("::")[-1]
        url = url.split("::")[-2]
    file_id = url.split("/")[-1]

    with _cloudscraper_session() as s:
        res = s.get(url)
        try:
            from lxml.etree import HTML as lhtml
            html = lhtml(res.text)
        except ImportError:
            raise DirectLinkException("EasyUpload: lxml required.")

        if html.xpath("//h6[contains(text(),'Password Protected')]") and not pwd:
            raise DirectLinkException(f"EasyUpload: {PASSWORD_ERROR}")

        m = search(r"https://eu(?:[1-9][0-9]?|100)\.easyupload\.io/action\.php", res.text)
        if not m:
            raise DirectLinkException("EasyUpload: action URL not found.")
        action_url = m.group()

        s.headers.update({"referer": "https://easyupload.io/"})
        params = {
            "k": "6LfWajMdAAAAAGLXz_nxz2tHnuqa-abQqC97DIZ3",
            "ar": "1", "co": "aHR0cHM6Ly9lYXN5dXBsb2FkLmlvOjQ0Mw..",
            "hl": "en", "v": "0hCdE87LyjzAkFO5Ff-v7Hj1", "size": "invisible",
            "cb": "c3o1vbaxbmwe",
        }
        token = _get_captcha_token(s, params)
        if not token:
            raise DirectLinkException("EasyUpload: captcha failed.")

        data = {"type": "download-token", "url": file_id, "value": pwd,
                "captchatoken": token, "method": "regular"}
        result = s.post(action_url, data=data).json()

    if "download_link" in result:
        return result["download_link"]
    raise DirectLinkException(f"EasyUpload: {result.get('data', 'unknown error')}")


def _fichier(url: str) -> str:
    pwd = None
    if "::" in url:
        pwd = url.split("::")[-1]
        url = url.split("::")[-2]

    cget = _cloudscraper_session().request
    try:
        req = cget("post", url) if not pwd else cget("post", url, data={"pass": pwd})
    except Exception as e:
        raise DirectLinkException(f"1Fichier: request error — {e}")

    if req.status_code == 404:
        raise DirectLinkException("1Fichier: file not found.")

    try:
        from lxml.etree import HTML as lhtml
        html = lhtml(req.text)
    except ImportError:
        raise DirectLinkException("1Fichier: lxml required.")

    dl = html.xpath('//a[@class="ok btn-general btn-orange"]/@href')
    if dl:
        return dl[0]

    warn = html.xpath('//div[@class="ct_warn"]')
    if len(warn) >= 3:
        txt = warn[-1].text or ""
        if "you must wait" in txt.lower():
            raise DirectLinkException(f"1Fichier: rate limited — {txt.strip()}")
        if "protect access" in txt.lower():
            raise DirectLinkException(f"1Fichier: {PASSWORD_ERROR}")
    raise DirectLinkException("1Fichier: could not generate direct link.")


def _filelions_streamwish(url: str) -> str:
    from config import FILELION_API, STREAMWISH_API
    domain = urlparse(url).hostname or ""
    parsed = urlparse(url)
    scheme = parsed.scheme

    _lions = {"filelions.co","filelions.live","filelions.to","filelions.site",
               "cabecabean.lol","filelions.online","mycloudz.cc"}
    if any(x in domain for x in _lions):
        api_key = FILELION_API
        api_url = "https://vidhideapi.com"
    else:
        api_key = STREAMWISH_API
        api_url = "https://api.streamwish.com"

    if not api_key:
        raise DirectLinkException(
            f"API key not set for {domain}. Add FILELION_API or STREAMWISH_API to config."
        )

    file_code = url.split("/")[-1]
    quality = ""
    if file_code.endswith(("_o", "_h", "_n", "_l")):
        parts = file_code.rsplit("_", 1)
        quality = parts[1]
        file_code = parts[0]

    res = requests.get(
        f"{api_url}/api/file/direct_link",
        params={"key": api_key, "file_code": file_code, "hls": "1"}
    ).json()

    if res.get("status") != 200:
        raise DirectLinkException(f"FileLions/StreamWish: {res.get('msg')}")

    versions = res["result"].get("versions", [])
    if not versions:
        raise DirectLinkException("FileLions/StreamWish: file not found.")

    available = ""
    for v in versions:
        if quality and quality == v["name"]:
            return v["url"]
        labels = {"l": "Low", "n": "Normal", "o": "Original", "h": "HD"}
        available += f"\n  {labels.get(v['name'], v['name'])} → `{scheme}://{domain}/{file_code}_{v['name']}`"

    raise DirectLinkException(
        f"FileLions/StreamWish: specify quality.{available}"
    )


def _fuckingfast(url: str) -> str:
    try:
        res = requests.get(url)
        m = search(r"window\.open\(([\"'])(https://fuckingfast\.co/dl/[^\"']+)\1", res.text)
        if not m:
            raise DirectLinkException("FuckingFast: download link not found.")
        return m.group(2)
    except DirectLinkException:
        raise
    except Exception as e:
        raise DirectLinkException(f"FuckingFast: {e}")


def _github(url: str) -> str:
    if not search(r"\bhttps?://.*github\.com.*releases\S+", url):
        raise DirectLinkException("GitHub: not a releases link.")
    with _cloudscraper_session() as s:
        res = s.get(url, stream=True, allow_redirects=False)
        if "location" in res.headers:
            return res.headers["location"]
    raise DirectLinkException("GitHub: could not extract direct link.")


def _gofile(url: str) -> str | tuple:
    pwd_hash = ""
    if "::" in url:
        raw_pwd = url.split("::")[-1]
        pwd_hash = sha256(raw_pwd.encode()).hexdigest()
        url = url.split("::")[-2]
    file_id = url.split("/")[-1]

    with _session_with_retries() as s:
        token_res = s.post("https://api.gofile.io/accounts").json()
        if token_res.get("status") != "ok":
            raise DirectLinkException("GoFile: failed to get token.")
        token = token_res["data"]["token"]

        api = f"https://api.gofile.io/contents/{file_id}?cache=true"
        if pwd_hash:
            api += f"&password={pwd_hash}"

        headers = {
            "User-Agent": USER_AGENT,
            "Authorization": f"Bearer {token}",
        }
        data = s.get(api, headers=headers).json()

    status = data.get("status", "")
    if "error-password" in status:
        raise DirectLinkException(f"GoFile: {PASSWORD_ERROR}")
    if "error-notFound" in status:
        raise DirectLinkException("GoFile: file not found.")
    if "error-notPublic" in status:
        raise DirectLinkException("GoFile: folder is not public.")

    contents = data["data"]["children"]
    details = {
        "title": data["data"].get("name", file_id),
        "total_size": 0,
        "contents": [],
        "header": f"Cookie: accountToken={token}",
    }

    def _collect(folder_contents: dict, path_prefix: str):
        for item in folder_contents.values():
            if item["type"] == "folder" and item.get("public"):
                _collect(item["children"], f"{path_prefix}/{item['name']}")
            elif item["type"] == "file":
                details["contents"].append({
                    "path": path_prefix,
                    "filename": item["name"],
                    "url": item["link"],
                })
                details["total_size"] += item.get("size", 0)

    _collect(contents, details["title"])

    if len(details["contents"]) == 1:
        return (details["contents"][0]["url"], details["header"])
    return details


def _hxfile(url: str) -> tuple[str, list]:
    if not ospath.isfile("hxfile.txt"):
        raise DirectLinkException("HxFile: hxfile.txt (cookies) not found.")
    jar = MozillaCookieJar()
    jar.load("hxfile.txt")
    cookies = {c.name: c.value for c in jar}
    if url.strip().endswith(".html"):
        url = url[:-5]
    file_code = url.split("/")[-1]
    try:
        from lxml.etree import HTML as lhtml
        html = lhtml(requests.post(url, data={"op": "download2", "id": file_code}, cookies=cookies).text)
        link = html.xpath("//a[@class='btn btn-dow']/@href")
    except ImportError:
        raise DirectLinkException("HxFile: lxml required.")
    if not link:
        raise DirectLinkException("HxFile: direct link not found.")
    return link[0], [f"Referer: {url}"]


def _krakenfiles(url: str) -> str:
    with _session_with_retries() as s:
        try:
            from lxml.etree import HTML as lhtml
            html = lhtml(s.get(url).text)
        except ImportError:
            raise DirectLinkException("KrakenFiles: lxml required.")

        post_url = html.xpath('//form[@id="dl-form"]/@action')
        token = html.xpath('//input[@id="dl-token"]/@value')
        if not post_url or not token:
            raise DirectLinkException("KrakenFiles: form data not found.")

        result = s.post(f"https://krakenfiles.com{post_url[0]}", data={"token": token[0]}).json()

    if result.get("status") != "ok":
        raise DirectLinkException("KrakenFiles: download failed.")
    return result["url"]


def _linkbox(url: str) -> str | dict:
    token = urlparse(url).path.split("/")[-1]
    details = {"contents": [], "title": "", "total_size": 0}

    def _fetch_single(item_id: str):
        res = requests.get("https://www.linkbox.to/api/file/detail",
                           params={"itemId": item_id}).json()
        data = res.get("data", {})
        if not data or not data.get("itemInfo"):
            raise DirectLinkException("LinkBox: item info not found.")
        info = data["itemInfo"]
        name = info["name"]
        ext = info.get("sub_type", "")
        if ext and not name.endswith(ext):
            name += f".{ext}"
        details["title"] = details["title"] or name
        details["total_size"] += int(info.get("size", 0))
        details["contents"].append({"path": "", "filename": name, "url": info["url"]})

    def _fetch_folder(pid: int = 0, folder_path: str = ""):
        res = requests.get("https://www.linkbox.to/api/file/share_out_list",
                           params={"shareToken": token, "pageSize": 1000, "pid": pid}).json()
        data = res.get("data", {})
        if not data:
            raise DirectLinkException("LinkBox: data not found.")
        if data.get("shareType") == "singleItem":
            return _fetch_single(data["itemId"])
        details["title"] = details["title"] or data.get("dirName", token)
        for item in data.get("list", []):
            if item["type"] == "dir" and "url" not in item:
                new_path = f"{folder_path}/{item['name']}" if folder_path else item["name"]
                _fetch_folder(item["id"], new_path)
            elif "url" in item:
                name = item["name"]
                ext = item.get("sub_type", "")
                if ext and not name.endswith(ext):
                    name += f".{ext}"
                details["contents"].append({
                    "path": folder_path or details["title"],
                    "filename": name, "url": item["url"],
                })
                details["total_size"] += int(item.get("size", 0))

    _fetch_folder()
    if not details["contents"]:
        raise DirectLinkException("LinkBox: no files found.")
    if len(details["contents"]) == 1:
        return details["contents"][0]["url"]
    return details


def _lulacloud(url: str) -> str:
    try:
        res = requests.post(url, headers={"Referer": url}, allow_redirects=False)
        return res.headers["location"]
    except Exception as e:
        raise DirectLinkException(f"LulaCloud: {e}")


def _mediafile(url: str) -> str:
    try:
        res = requests.get(url, allow_redirects=True)
        m = search(r"href='([^']+)'", res.text)
        if not m:
            raise DirectLinkException("MediaFile: download link not found.")
        download_url = m.group(1)
        sleep(60)
        res2 = requests.get(download_url, headers={"Referer": url}, cookies=res.cookies)
        pv = search(r"showFileInformation(.*);", res2.text)
        if not pv:
            raise DirectLinkException("MediaFile: post value not found.")
        post_id = pv.group(1).strip("()")
        resp = requests.post(
            "https://mediafile.cc/account/ajax/file_details",
            data={"u": post_id},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        html = resp.json()["html"]
        links = [l for l in findall(r'https://[^\s"\']+', html) if "download_token" in l]
        if len(links) < 2:
            raise DirectLinkException("MediaFile: download token link not found.")
        return links[1]
    except DirectLinkException:
        raise
    except Exception as e:
        raise DirectLinkException(f"MediaFile: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# MEDIAFIRE — File + Folder handler  (v3 — multi-fallback)
# ─────────────────────────────────────────────────────────────────────────────

def _mf_resolve_indirect(href: str, pwd: str | None, session) -> str:
    """
    Some MediaFire buttons point to an intermediate /file/ page instead of a
    direct CDN URL. This helper follows one such hop if needed.

    - If href is already a CDN URL    → return as-is
    - If href is another /file/ page  → re-scrape it (one level deep only)
    - If href starts with '//'        → prepend https:
    """
    if not href:
        return href

    # Fix protocol-relative URLs
    if href.startswith("//"):
        href = f"https:{href}"

    # Already a CDN direct link → return immediately
    if search(r"download\d*\.mediafire\.com", href):
        return href

    # Another mediafire.com/file/ page — follow it once (one hop only)
    if "mediafire.com/file/" in href:
        try:
            from lxml.etree import HTML as lhtml
            res       = session.get(href, timeout=20)
            html_tree = lhtml(res.text)

            # Try all XPath selectors on the inner page
            for selector in [
                '//a[@id="downloadButton"]/@href',
                '//a[@aria-label="Download file"]/@href',
                '//a[contains(@class,"popsok")]/@href',
            ]:
                sub_link = html_tree.xpath(selector)
                if sub_link and sub_link[0].startswith("http"):
                    return sub_link[0]

            # Regex fallback on the inner page raw HTML
            raw = findall(
                r'https?://download\d*\.mediafire\.com/[^"\'<>\s]+', res.text
            )
            if raw:
                return raw[0]
        except Exception:
            pass

    return href


def _mediafire(url: str, session=None) -> str | dict:
    """
    Resolve a MediaFire file/folder URL to a direct download link.

    Supports:
      - Regular file links
      - Password-protected files  (url::password)
      - Folder links              (/folder/ in URL)
      - Already-direct CDN links  (download\d+.mediafire.com)

    Fallback chain (file links):
      1. XPath  → a#downloadButton              (current primary button — 2024/2025)
      2. XPath  → a[aria-label="Download file"] (legacy selector)
      3. XPath  → a[contains(@class,"popsok")] (alternate class used on some pages)
      4. XPath  → broad href scan for any mediafire download link
      5. Regex  → raw HTML scan for download CDN URLs
      6. MediaFire Public API v1.5              (no HTML parsing — most robust)
    """
    # ── Folder → delegate ─────────────────────────────────────────────────────
    if "/folder/" in url:
        return _mediafire_folder(url)

    # ── Password split ────────────────────────────────────────────────────────
    pwd = None
    if "::" in url:
        pwd = url.split("::")[-1]
        url = url.split("::")[-2]

    # ── Already a direct CDN URL → return immediately ─────────────────────────
    direct_match = findall(
        r"https?://download\d*\.mediafire\.com/[^\s\"'<>]+", url
    )
    if direct_match:
        return direct_match[0]

    # ── Build clean URL (strip query params) ──────────────────────────────────
    parsed    = urlparse(url)
    clean_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"

    if session is None:
        session = _cloudscraper_session()

    # ─────────────────────────────────────────────────────────────────────────
    # STEP 1 — Fetch the MediaFire page
    # ─────────────────────────────────────────────────────────────────────────
    try:
        response  = session.get(
            clean_url,
            timeout=20,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
                "Referer": "https://www.mediafire.com/",
            },
        )
        page_text = response.text
    except Exception as e:
        raise DirectLinkException(f"MediaFire: failed to fetch page — {e}")

    # ─────────────────────────────────────────────────────────────────────────
    # STEP 2 — Parse HTML with lxml
    # ─────────────────────────────────────────────────────────────────────────
    html_tree = None
    try:
        from lxml.etree import HTML as lhtml
        html_tree = lhtml(page_text)
    except ImportError:
        pass  # lxml not installed → skip XPath fallbacks, go to regex/API

    # ── Error page detection ──────────────────────────────────────────────────
    if html_tree is not None:
        # Official error paragraph
        mf_errors = html_tree.xpath('//p[@class="notranslate"]/text()')
        if mf_errors:
            raise DirectLinkException(f"MediaFire: {mf_errors[0].strip()}")

        # Generic error text in divs
        for err_text in html_tree.xpath('//div[contains(@class,"error")]//text()'):
            err_lower = err_text.lower().strip()
            if err_lower and any(
                kw in err_lower
                for kw in ("invalid", "removed", "not found", "doesn't exist", "unavailable")
            ):
                raise DirectLinkException(f"MediaFire: {err_text.strip()}")

    # ── Password-protected page detection ─────────────────────────────────────
    _pw_indicators = [
        "passwordPrompt",
        "password_container",
        'name="downloadp"',
        "This file is password protected",
    ]
    _is_password_page = (
        any(ind in page_text for ind in _pw_indicators)
        or (
            html_tree is not None
            and (
                html_tree.xpath("//div[contains(@class,'passwordPrompt')]")
                or html_tree.xpath("//div[contains(@class,'password_container')]")
                or html_tree.xpath("//input[@name='downloadp']")
            )
        )
    )

    if _is_password_page:
        if not pwd:
            raise DirectLinkException(f"MediaFire: {PASSWORD_ERROR}")

        # Submit password via POST
        try:
            resp2     = session.post(
                clean_url,
                data={"downloadp": pwd},
                timeout=20,
            )
            page_text = resp2.text
            if html_tree is not None:
                html_tree = lhtml(page_text)
        except Exception as e:
            raise DirectLinkException(f"MediaFire: password submit failed — {e}")

        # Still on password page → wrong password
        if any(ind in page_text for ind in _pw_indicators):
            raise DirectLinkException("MediaFire: wrong password.")

    # ─────────────────────────────────────────────────────────────────────────
    # FALLBACK 1 — a#downloadButton  (MediaFire's current primary button 2024/25)
    # ─────────────────────────────────────────────────────────────────────────
    if html_tree is not None:
        link = html_tree.xpath('//a[@id="downloadButton"]/@href')
        if link and link[0].strip():
            resolved = _mf_resolve_indirect(link[0].strip(), pwd, session)
            if resolved:
                return resolved

    # ─────────────────────────────────────────────────────────────────────────
    # FALLBACK 2 — aria-label="Download file"  (legacy selector)
    # ─────────────────────────────────────────────────────────────────────────
    if html_tree is not None:
        link = html_tree.xpath('//a[@aria-label="Download file"]/@href')
        if link and link[0].strip():
            resolved = _mf_resolve_indirect(link[0].strip(), pwd, session)
            if resolved:
                return resolved

    # ─────────────────────────────────────────────────────────────────────────
    # FALLBACK 3 — a[contains(@class,"popsok")]  (alternate button class)
    # ─────────────────────────────────────────────────────────────────────────
    if html_tree is not None:
        link = html_tree.xpath('//a[contains(@class,"popsok")]/@href')
        if link and link[0].strip():
            resolved = _mf_resolve_indirect(link[0].strip(), pwd, session)
            if resolved:
                return resolved

    # ─────────────────────────────────────────────────────────────────────────
    # FALLBACK 4 — Broad href scan: any <a> linking to a mediafire download
    # ─────────────────────────────────────────────────────────────────────────
    if html_tree is not None:
        for href in html_tree.xpath("//a/@href"):
            href = (href or "").strip()
            if href and "mediafire.com" in href and "download" in href.lower():
                resolved = _mf_resolve_indirect(href, pwd, session)
                if resolved:
                    return resolved

    # ─────────────────────────────────────────────────────────────────────────
    # FALLBACK 5 — Raw HTML regex: scan for CDN download URLs
    # ─────────────────────────────────────────────────────────────────────────
    raw_cdn_links = findall(
        r'https?://download\d*\.mediafire\.com/[^"\'<>\s\\]+',
        page_text,
    )
    if raw_cdn_links:
        return raw_cdn_links[0]

    # ─────────────────────────────────────────────────────────────────────────
    # FALLBACK 6 — MediaFire Public API v1.5
    #   Uses the file's quick_key extracted from the URL.
    #   Returns the direct normal_download link without HTML scraping.
    # ─────────────────────────────────────────────────────────────────────────
    key_match = search(r"/file/([a-zA-Z0-9]+)", clean_url)
    if key_match:
        file_key = key_match.group(1)
        try:
            api_res = session.get(
                "https://www.mediafire.com/api/1.5/file/get_links.php",
                params={
                    "quick_key":      file_key,
                    "link_type":      "normal_download",
                    "response_format": "json",
                },
                timeout=15,
            ).json()

            resp_data  = api_res.get("response", {})
            api_result = resp_data.get("result", "")

            if api_result == "Success":
                links_list = resp_data.get("links", [])
                if links_list:
                    dl_link = links_list[0].get("normal_download", "")
                    if dl_link:
                        resolved = _mf_resolve_indirect(dl_link, pwd, session)
                        if resolved:
                            return resolved

            elif api_result == "Error":
                api_error = resp_data.get("message", "unknown API error")
                LOGGER.warning(f"[MediaFire] API error for key={file_key}: {api_error}")

        except Exception as api_exc:
            LOGGER.warning(f"[MediaFire] API fallback failed: {api_exc}")

    # ── All 6 fallbacks exhausted ─────────────────────────────────────────────
    raise DirectLinkException(
        "MediaFire: download link পাওয়া যায়নি।\n"
        "সম্ভাব্য কারণ:\n"
        "  • File টি মুছে ফেলা হয়েছে\n"
        "  • File টি private করা হয়েছে\n"
        "  • MediaFire তাদের HTML structure পুনরায় পরিবর্তন করেছে\n"
        "  • Temporary server error"
    )


def _mediafire_folder(url: str) -> dict | str:
    """
    Resolve a MediaFire folder URL to a structured dict of direct download links.

    Uses MediaFire's public API v1.5 for:
      - Folder metadata (name, key)
      - File listing with pagination (chunk-based, 100 files per chunk)
      - Sub-folder recursion
      - Per-file direct link resolution via _mediafire()

    Returns:
        str  — if folder contains exactly 1 file (returns its direct URL)
        dict — {"title", "total_size", "contents": [{"filename","path","url"}]}
    """
    # ── Password split ────────────────────────────────────────────────────────
    pwd = None
    if "::" in url:
        pwd = url.split("::")[-1]
        url = url.split("::")[-2]

    # ── Extract folder key(s) from URL ────────────────────────────────────────
    # Handles both:
    #   /folder/abc123def456/FolderName
    #   /folder/abc123,def456,ghi789  (multi-folder)
    path_part   = urlparse(url).path
    after_folder = path_part.split("/folder/")[-1]
    # First path segment only (ignore /FolderName suffix)
    first_segment = after_folder.split("/")[0]
    raw_keys      = first_segment.split(",")
    folder_keys   = [k.strip() for k in raw_keys if k.strip()]

    if not folder_keys:
        raise DirectLinkException(
            "MediaFire: URL থেকে folder key বের করা যায়নি।"
        )

    details = {
        "contents":   [],
        "title":      "",
        "total_size": 0,
        "header":     "",
    }

    session = _cloudscraper_session()

    # ─────────────────────────────────────────────────────────────────────────
    # INNER HELPERS
    # ─────────────────────────────────────────────────────────────────────────

    def _resolve_file_link(file_info: dict) -> str:
        """
        Given the 'file' dict from the API, attempt to get a direct CDN URL.

        Priority:
          1. If 'direct_download' key exists and is a CDN URL → use directly
          2. normal_download link  → resolve via _mediafire() (may be a page)
          3. view link             → resolve via _mediafire()
          4. Return empty string on total failure
        """
        links = file_info.get("links", {})

        # Priority 1: direct CDN link from API (not always present)
        direct = links.get("direct_download", "")
        if direct and "download" in direct and "mediafire.com" in direct:
            return direct

        # Priority 2 & 3: normal_download or view page → resolve
        for link_key in ("normal_download", "view"):
            page_link = links.get(link_key, "")
            if not page_link:
                continue
            try:
                pwd_suffix = f"::{pwd}" if pwd else ""
                resolved   = _mediafire(page_link + pwd_suffix, session)
                if resolved and isinstance(resolved, str):
                    return resolved
            except DirectLinkException:
                continue
            except Exception:
                continue

        return ""

    def _collect_files(folder_key: str, folder_path: str) -> None:
        """Paginate through all files in a folder and add them to details."""
        chunk        = 1
        total_chunks = 1   # Will be updated after first API call

        while chunk <= total_chunks:
            try:
                res = session.get(
                    "https://www.mediafire.com/api/1.5/folder/get_content.php",
                    params={
                        "content_type":    "files",
                        "folder_key":      folder_key,
                        "chunk":           chunk,
                        "chunk_size":      100,
                        "order_by":        "name",
                        "order_direction": "asc",
                        "response_format": "json",
                    },
                    timeout=20,
                )
                data = res.json()
            except Exception as exc:
                LOGGER.warning(f"[MediaFire folder] API chunk {chunk} failed: {exc}")
                break

            resp_obj       = data.get("response", {})
            folder_content = resp_obj.get("folder_content", {})

            if resp_obj.get("result") != "Success":
                LOGGER.warning(
                    f"[MediaFire folder] API result not Success: "
                    f"{resp_obj.get('message', 'unknown')}"
                )
                break

            # Update total chunk count from first response
            try:
                total_chunks = int(folder_content.get("chunks", total_chunks))
            except (ValueError, TypeError):
                pass

            for f in folder_content.get("files", []):
                filename   = f.get("filename") or f"file_{len(details['contents']) + 1}"
                direct_url = _resolve_file_link(f)

                if direct_url:
                    details["contents"].append({
                        "filename": filename,
                        "path":     folder_path,
                        "url":      direct_url,
                    })
                    try:
                        details["total_size"] += int(f.get("size", 0))
                    except (ValueError, TypeError):
                        pass
                else:
                    LOGGER.warning(
                        f"[MediaFire folder] Could not resolve link for: {filename}"
                    )

            chunk += 1

    def _collect_subfolders(folder_key: str, parent_path: str) -> None:
        """Recursively collect all sub-folders and their files."""
        chunk        = 1
        total_chunks = 1

        while chunk <= total_chunks:
            try:
                res = session.get(
                    "https://www.mediafire.com/api/1.5/folder/get_content.php",
                    params={
                        "content_type":    "folders",
                        "folder_key":      folder_key,
                        "chunk":           chunk,
                        "chunk_size":      100,
                        "response_format": "json",
                    },
                    timeout=20,
                )
                data = res.json()
            except Exception as exc:
                LOGGER.warning(f"[MediaFire folder] Sub-folder API chunk {chunk} failed: {exc}")
                break

            resp_obj       = data.get("response", {})
            folder_content = resp_obj.get("folder_content", {})

            if resp_obj.get("result") != "Success":
                break

            try:
                total_chunks = int(folder_content.get("chunks", total_chunks))
            except (ValueError, TypeError):
                pass

            for sub in folder_content.get("folders", []):
                sub_key  = sub.get("folderkey", "")
                sub_name = sub.get("name", sub_key)

                if not sub_key:
                    continue

                # Build nested path
                new_path = (
                    f"{parent_path}/{sub_name}" if parent_path else sub_name
                )

                # Recurse: files first, then deeper sub-folders
                _collect_files(sub_key, new_path)
                _collect_subfolders(sub_key, new_path)

            chunk += 1

    # ─────────────────────────────────────────────────────────────────────────
    # MAIN LOOP — process each folder key
    # ─────────────────────────────────────────────────────────────────────────
    for fkey in folder_keys:
        # ── Get folder metadata (name) ────────────────────────────────────────
        try:
            info_res = session.post(
                "https://www.mediafire.com/api/1.5/folder/get_info.php",
                data={
                    "folder_key":      fkey,
                    "response_format": "json",
                },
                timeout=15,
            ).json()

            folder_info = (
                info_res
                .get("response", {})
                .get("folder_info", {})
            )

            if not details["title"]:
                details["title"] = folder_info.get("name") or fkey

        except Exception as exc:
            LOGGER.warning(f"[MediaFire folder] get_info failed for key={fkey}: {exc}")
            if not details["title"]:
                details["title"] = fkey

        # ── Collect all files (flat + recursive sub-folders) ──────────────────
        root_path = details["title"]
        _collect_files(fkey, root_path)
        _collect_subfolders(fkey, root_path)

    # ─────────────────────────────────────────────────────────────────────────
    # RESULT
    # ─────────────────────────────────────────────────────────────────────────
    if not details["contents"]:
        raise DirectLinkException(
            "MediaFire: folder-এ কোনো file পাওয়া যায়নি।\n"
            "সম্ভাব্য কারণ:\n"
            "  • Folder টি empty\n"
            "  • Folder টি private বা password-protected\n"
            "  • সব file এর link resolve করা ব্যর্থ হয়েছে"
        )

    # Single file in folder → return plain URL string
    if len(details["contents"]) == 1:
        return details["contents"][0]["url"]

    return details


def _mp4upload(url: str) -> tuple[str, list]:
    with _session_with_retries() as s:
        try:
            from lxml.etree import HTML as lhtml
        except ImportError:
            raise DirectLinkException("mp4upload: lxml required.")

        url = url.replace("embed-", "")
        html = lhtml(s.get(url).text)
        inputs = html.xpath("//input")
        data = {i.get("name"): i.get("value") for i in inputs}
        if not data:
            raise DirectLinkException("mp4upload: file not found.")

        res2 = s.post(url, data=data, headers={
            "User-Agent": USER_AGENT, "Referer": "https://www.mp4upload.com/"
        }).text
        html2 = lhtml(res2)
        inputs2 = html2.xpath('//form[@name="F1"]//input')
        data2 = {i.get("name"): i.get("value", "").replace(" ", "") for i in inputs2}
        if not data2:
            raise DirectLinkException("mp4upload: file not found (step 2).")
        data2["referer"] = url
        direct = s.post(url, data=data2).url

    return direct, ["Referer: https://www.mp4upload.com/"]


def _onedrive(url: str) -> str:
    with _cloudscraper_session() as s:
        url = s.get(url).url
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        folder_id = qs.get("resid", [None])[0]
        authkey = qs.get("authkey", [None])[0]
        if not folder_id or not authkey:
            raise DirectLinkException("OneDrive: could not parse folder ID or auth key.")

        boundary = uuid4()
        headers = {"content-type": f"multipart/form-data;boundary={boundary}"}
        data = (
            f"--{boundary}\r\nContent-Disposition: form-data;name=data\r\n"
            f"Prefer: Migration=EnableRedirect;FailOnMigratedFiles\r\n"
            f"X-HTTP-Method-Override: GET\r\nContent-Type: application/json\r\n\r\n"
            f"--{boundary}--"
        )
        api = (
            f"https://api.onedrive.com/v1.0/drives/{folder_id.split('!', 1)[0]}"
            f"/items/{folder_id}?$select=id,@content.downloadUrl"
            f"&ump=1&authKey={authkey}"
        )
        resp = s.get(api, headers=headers, data=data).json()

    if "@content.downloadUrl" not in resp:
        raise DirectLinkException("OneDrive: direct link not found.")
    return resp["@content.downloadUrl"]


def _osdn(url: str) -> str:
    with _cloudscraper_session() as s:
        try:
            from lxml.etree import HTML as lhtml
            html = lhtml(s.get(url).text)
            link = html.xpath('//a[@class="mirror_link"]/@href')
        except ImportError:
            raise DirectLinkException("OSDN: lxml required.")
    if not link:
        raise DirectLinkException("OSDN: direct link not found.")
    return f"https://osdn.net{link[0]}"


def _pcloud(url: str) -> str:
    with _cloudscraper_session() as s:
        res = s.get(url).text
    m = findall(r".downloadlink.:..(https:.*)..", res)
    if not m:
        raise DirectLinkException("pCloud: direct link not found.")
    return m[0].replace(r"\/", "/")


def _pixeldrain(url: str) -> str:
    url = url.rstrip("/")
    code = url.split("/")[-1].split("?", 1)[0]
    try:
        res = requests.get("https://pd.cybar.xyz/", allow_redirects=True)
        return res.url + code
    except Exception as e:
        raise DirectLinkException(f"Pixeldrain: {e}")


def _qiwi(url: str) -> str:
    file_id = url.split("/")[-1]
    try:
        from lxml.etree import HTML as lhtml
        tree = lhtml(requests.get(url).text)
        names = tree.xpath('//h1[@class="page_TextHeading__VsM7r"]/text()')
        if not names:
            raise DirectLinkException("qiwi.gg: file not found.")
        ext = names[0].split(".")[-1]
        return f"https://spyderrock.com/{file_id}.{ext}"
    except ImportError:
        raise DirectLinkException("qiwi.gg: lxml required.")


def _racaty(url: str) -> str:
    with _cloudscraper_session() as s:
        url = s.get(url).url
        try:
            from lxml.etree import HTML as lhtml
            html = lhtml(s.post(url, data={"op": "download2", "id": url.split("/")[-1]}).text)
            link = html.xpath("//a[@id='uniqueExpirylink']/@href")
        except ImportError:
            raise DirectLinkException("Racaty: lxml required.")
    if not link:
        raise DirectLinkException("Racaty: direct link not found.")
    return link[0]


def _send_cm(url: str) -> str | tuple:
    def _send_cm_file(file_url: str, file_id: str = None) -> tuple[str, list]:
        pwd = None
        if "::" in file_url:
            pwd = file_url.split("::")[-1]
            file_url = file_url.split("::")[-2]
        needs_pwd = False

        with _cloudscraper_session() as s:
            if file_id is None:
                try:
                    from lxml.etree import HTML as lhtml
                    html = lhtml(s.get(file_url).text)
                    needs_pwd = bool(html.xpath("//input[@name='password']"))
                    ids = html.xpath("//input[@name='id']/@value")
                    if not ids:
                        raise DirectLinkException("Send.cm: file ID not found.")
                    file_id = ids[0]
                except ImportError:
                    raise DirectLinkException("Send.cm: lxml required.")

            data = {"op": "download2", "id": file_id}
            if pwd and needs_pwd:
                data["password"] = pwd
            res = s.post("https://send.cm/", data=data, allow_redirects=False)
            if "Location" in res.headers:
                return res.headers["Location"], ["Referer: https://send.cm/"]

        if needs_pwd:
            raise DirectLinkException(f"Send.cm: {PASSWORD_ERROR}")
        raise DirectLinkException("Send.cm: direct link not found.")

    if "/d/" in url:
        return _send_cm_file(url)
    if "/s/" not in url:
        return _send_cm_file(url, url.split("/")[-1])

    # Folder
    details = {"contents": [], "title": url.split("/")[-1], "total_size": 0,
               "header": "Referer: https://send.cm/"}

    def _get_folder_links(folder_url: str, folder_path: str = ""):
        try:
            from cloudscraper import create_scraper
            cs = create_scraper()
            from lxml.etree import HTML as lhtml
            html = lhtml(requests.get(folder_url).text)
            for href, fname, size_txt in zip(
                html.xpath("//h6/a/@href"),
                html.xpath("//h6/a/text()"),
                html.xpath("//tr[@class='selectable']//span/text()")
            ):
                sub_id = href.split("/")[-1]
                try:
                    link_res = cs.get(href, allow_redirects=False)
                    dl = link_res.headers.get("Location")
                    if dl:
                        for unit, factor in [("GB", 1e9), ("MB", 1e6), ("KB", 1024)]:
                            if unit in size_txt:
                                details["total_size"] += float(size_txt.replace(unit, "").strip()) * factor
                                break
                        details["contents"].append({
                            "url": dl, "filename": fname.strip(), "path": folder_path
                        })
                except Exception:
                    pass
        except Exception:
            pass

    _get_folder_links(url, details["title"])
    if not details["contents"]:
        raise DirectLinkException("Send.cm: folder is empty or inaccessible.")
    if len(details["contents"]) == 1:
        return (details["contents"][0]["url"], [details["header"]])
    return details


def _shrdsk(url: str) -> str:
    with _cloudscraper_session() as s:
        short_id = url.split("/")[-1]
        data = s.get(
            f"https://us-central1-affiliate2apk.cloudfunctions.net/get_data?shortid={short_id}"
        ).json()
        if "download_data" not in data:
            raise DirectLinkException("Shrdsk: download data not found.")
        res = s.get(f"https://shrdsk.me/download/{data['download_data']}", allow_redirects=False)
        if "Location" in res.headers:
            return res.headers["Location"]
    raise DirectLinkException("Shrdsk: redirect location not found.")


def _solidfiles(url: str) -> str:
    with _cloudscraper_session() as s:
        src = s.get(url).text
        m = search(r"viewerOptions\'\,\ (.*?)\)\;", src)
        if not m:
            raise DirectLinkException("Solidfiles: viewer options not found.")
        return loads(m.group(1))["downloadUrl"]


def _streamhub(url: str) -> str:
    file_code = url.split("/")[-1]
    parsed = urlparse(url)
    dl_url = f"{parsed.scheme}://{parsed.hostname}/d/{file_code}"
    with _cloudscraper_session() as s:
        try:
            from lxml.etree import HTML as lhtml
        except ImportError:
            raise DirectLinkException("StreamHub: lxml required.")
        html = lhtml(s.get(dl_url).text)
        inputs = html.xpath('//form[@name="F1"]//input')
        data = {i.get("name"): i.get("value") for i in inputs}
        if not data:
            raise DirectLinkException("StreamHub: no form inputs found.")
        s.headers.update({"referer": dl_url})
        sleep(1)
        html2 = lhtml(s.post(dl_url, data=data).text)
        link = html2.xpath('//a[@class="btn btn-primary btn-go downloadbtn"]/@href')
        if not link:
            raise DirectLinkException("StreamHub: direct link not found.")
    return link[0]


def _streamtape(url: str) -> str:
    parts = url.split("/")
    vid_id = parts[4] if len(parts) >= 6 else parts[-1]
    try:
        from lxml.etree import HTML as lhtml
        html = lhtml(requests.get(url).text)
    except ImportError:
        raise DirectLinkException("StreamTape: lxml required.")

    script = (
        html.xpath("//script[contains(text(),'ideoooolink')]/text()") or
        html.xpath("//script[contains(text(),'ideoolink')]/text()")
    )
    if not script:
        raise DirectLinkException("StreamTape: required script not found.")
    link = findall(r"(&expires\S+)'", script[0])
    if not link:
        raise DirectLinkException("StreamTape: download link not found.")
    return f"https://streamtape.com/get_video?id={vid_id}{link[-1]}"


def _streamvid(url: str) -> str:
    file_code = url.split("/")[-1]
    parsed = urlparse(url)
    dl_url = f"{parsed.scheme}://{parsed.hostname}/d/{file_code}"
    quality_defined = url.strip().endswith(("_o", "_h", "_n", "_l"))

    with _cloudscraper_session() as s:
        try:
            from lxml.etree import HTML as lhtml
        except ImportError:
            raise DirectLinkException("StreamVid: lxml required.")
        html = lhtml(s.get(dl_url).text)

        if quality_defined:
            data = {i.get("name"): i.get("value") for i in html.xpath('//form[@id="F1"]//input')}
            html2 = lhtml(s.post(dl_url, data=data).text)
            script = html2.xpath('//script[contains(text(),"document.location.href")]/text()')
            if script:
                dl = findall(r'document\.location\.href="(.*)"', script[0])
                if dl:
                    return dl[0]
            raise DirectLinkException("StreamVid: quality not available.")

        qualities = html.xpath('//div[@id="dl_versions"]/a/@href')
        names = html.xpath('//div[@id="dl_versions"]/a/text()[2]')
        if qualities:
            options = "\n".join(
                f"  {n.strip()} → `{q}`" for q, n in zip(qualities, names)
            )
            raise DirectLinkException(f"StreamVid: specify quality.\n{options}")
    raise DirectLinkException("StreamVid: video not found.")


def _swisstransfer(url: str) -> str | tuple | dict:
    m = match(r"https://www\.swisstransfer\.com/d/([\w-]+)(?:\:\:(\w+))?", url)
    if not m:
        raise DirectLinkException("SwissTransfer: invalid link format.")
    transfer_id, pwd = m.groups()
    pwd = pwd or ""

    def _encode_pwd(p: str) -> str:
        return b64encode(p.encode()).decode() if p else ""

    def _get_token(cont_uuid: str, file_uuid: str) -> str:
        res = requests.post(
            "https://www.swisstransfer.com/api/generateDownloadToken",
            headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"},
            json={"password": pwd, "containerUUID": cont_uuid, "fileUUID": file_uuid},
        )
        if res.status_code == 200:
            return res.text.strip().strip('"')
        raise DirectLinkException(f"SwissTransfer: token error {res.status_code}")

    headers = {
        "User-Agent": USER_AGENT,
        "Authorization": _encode_pwd(pwd) if pwd else "",
    }
    res = requests.get(f"https://www.swisstransfer.com/api/links/{transfer_id}", headers=headers)
    if res.status_code != 200:
        raise DirectLinkException(f"SwissTransfer: fetch error {res.status_code}")

    data = res.json()["data"]
    cont_uuid = data["containerUUID"]
    host = data["downloadHost"]
    files = data["container"]["files"]
    total = sum(f["fileSizeInBytes"] for f in files)

    if len(files) == 1:
        f = files[0]
        token = _get_token(cont_uuid, f["UUID"])
        return (
            f"https://{host}/api/download/{transfer_id}/{f['UUID']}?token={token}",
            ["User-Agent:Mozilla/5.0"],
        )

    contents = []
    for f in files:
        token = _get_token(cont_uuid, f["UUID"])
        contents.append({
            "filename": f["fileName"],
            "path": "",
            "url": f"https://{host}/api/download/{transfer_id}/{f['UUID']}?token={token}",
        })

    return {"contents": contents, "title": data["container"].get("message") or "swisstransfer",
            "total_size": total, "header": "User-Agent:Mozilla/5.0"}


def _terabox(url: str) -> str | dict:
    if "/file/" in url:
        return url
    try:
        res = requests.post(
            "https://teraboxdl.site/api/proxy",
            json={"url": url},
            headers={"Referer": "https://teraboxdl.site/", "User-Agent": USER_AGENT},
            timeout=30,
        ).json()
    except Exception as e:
        raise DirectLinkException(f"TeraBox: API error — {e}")

    if res.get("errno") != 0 or not res.get("list"):
        raise DirectLinkException("TeraBox: file not found or link expired.")

    details = {"contents": [], "title": res["list"][0]["server_filename"], "total_size": 0}
    for item in res["list"]:
        details["contents"].append({
            "path": item.get("path", ""),
            "filename": item["server_filename"],
            "url": item["direct_link"],
        })
        details["total_size"] += item.get("size", 0)

    if len(details["contents"]) == 1:
        return details["contents"][0]["url"]
    return details


def _tmpsend(url: str) -> tuple[str, list]:
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    file_id = qs.get("d", [None])[0] or parsed.path.strip("/")
    if not file_id:
        raise DirectLinkException("TmpSend: invalid URL format.")
    return (
        f"https://tmpsend.com/download?d={file_id}",
        [f"Referer: https://tmpsend.com/thank-you?d={file_id}"],
    )


def _transfer_it(url: str) -> str:
    res = requests.post("https://transfer-it-henna.vercel.app/post", json={"url": url})
    if res.status_code == 200:
        return res.json()["url"]
    raise DirectLinkException("Transfer.it: file expired or not found.")


def _uploadee(url: str) -> str:
    with _cloudscraper_session() as s:
        try:
            from lxml.etree import HTML as lhtml
            html = lhtml(s.get(url).text)
            link = html.xpath("//a[@id='d_l']/@href")
        except ImportError:
            raise DirectLinkException("Upload.ee: lxml required.")
    if not link:
        raise DirectLinkException("Upload.ee: direct link not found.")
    return link[0]


def _uploadhaven(url: str) -> str:
    try:
        from lxml.etree import HTML as lhtml
        res = requests.get(url, headers={"Referer": "http://steamunlocked.net/"})
        html = lhtml(res.text)
        inputs = html.xpath('//form[@method="POST"]//input')
        data = {i.get("name"): i.get("value") for i in inputs}
        if not data:
            raise DirectLinkException("UploadHaven: no form inputs found.")
        sleep(15)
        res2 = requests.post(url, data=data, headers={"Referer": url}, cookies=res.cookies)
        html2 = lhtml(res2.text)
        links = html2.xpath('//div[@class="alert alert-success mb-0"]//a/@href')
        if not links:
            raise DirectLinkException("UploadHaven: direct link not found.")
        return links[0]
    except DirectLinkException:
        raise
    except ImportError:
        raise DirectLinkException("UploadHaven: lxml required.")
    except Exception as e:
        raise DirectLinkException(f"UploadHaven: {e}")


def _wetransfer(url: str) -> str:
    with _cloudscraper_session() as s:
        url = s.get(url).url
        parts = url.split("/")
        res = s.post(
            f"https://wetransfer.com/api/v4/transfers/{parts[-2]}/download",
            json={"security_hash": parts[-1], "intent": "entire_transfer"},
        ).json()
    if "direct_link" in res:
        return res["direct_link"]
    raise DirectLinkException(f"WeTransfer: {res.get('message', res.get('error', 'unknown'))}")


def _yandex_disk(url: str) -> str:
    links = findall(r"\b(https?://(yadi\.sk|disk\.yandex\.(com|ru))\S+)", url)
    if not links:
        raise DirectLinkException("Yandex Disk: no valid link found.")
    api = f"https://cloud-api.yandex.net/v1.0/disk/public/resources/download?public_key={links[0][0]}"
    res = requests.get(api).json()
    if "href" not in res:
        raise DirectLinkException("Yandex Disk: file not found or download limit reached.")
    return res["href"]


# ─────────────────────────────────────────────────────────────────────────────
# UTILITY: check if URL needs direct link resolution
# ─────────────────────────────────────────────────────────────────────────────

_SUPPORTED_DOMAINS = {
    "yadi.sk", "disk.yandex.", "buzzheavier.com", "devuploads", "lulacloud.com",
    "uploadhaven", "fuckingfast.co", "mediafile.cc", "mediafire.com", "osdn.net",
    "github.com", "transfer.it", "hxfile.co", "1drv.ms", "pixeldrain.com",
    "pixeldra.in", "racaty", "1fichier.com", "solidfiles.com", "krakenfiles.com",
    "upload.ee", "gofile.io", "send.cm", "tmpsend.com", "easyupload.io",
    "streamvid.net", "streamhub.ink", "streamhub.to", "u.pcloud.link", "qiwi.gg",
    "mp4upload.com", "berkasdrive.com", "swisstransfer.com", "akmfiles.com",
    "akmfls.xyz", "dood", "streamtape", "wetransfer.com", "we.tl", "terabox",
    "nephobox", "4funbox", "mirrobox", "teraboxapp", "filelions", "streamwish",
    "embedwish", "linkbox.to", "lbx.to", "shrdsk.me",
}


def is_supported_site(url: str) -> bool:
    """Return True if the URL's domain has a direct link handler."""
    domain = (urlparse(url).hostname or "").lower()
    return any(d in domain for d in _SUPPORTED_DOMAINS)
