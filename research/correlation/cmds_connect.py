"""CMDS Oracle helper via JDBC (thin oracledb hits DPY-3010 on this server)."""

from __future__ import annotations

import os
import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

import jaydebeapi
import pandas as pd

DEFAULT_DSN_HOST = os.environ.get("CMDS_HOST", "")
DEFAULT_DSN_PORT = int(os.environ.get("CMDS_PORT", "0") or "0")
DEFAULT_SERVICE = os.environ.get("CMDS_SERVICE", "cmds")
DEFAULT_JAR = Path(
    os.environ.get(
        "CMDS_OJDBC_JAR",
        str(Path(__file__).resolve().parent / "lib" / "ojdbc8.jar"),
    )
)


def _creds_from_docx(docx: Path) -> tuple[str, str]:
    with zipfile.ZipFile(docx) as z:
        root = ET.fromstring(z.read("word/document.xml"))
    texts = [
        t.text or ""
        for t in root.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t")
    ]
    full = "".join(texts)
    m_user = re.search(r"(?:用户名|账号|user)\s*[：:]?\s*(\S+)", full, re.I)
    m_pwd = re.search(r"密码[：:]\s*(\S+)", full)
    if not m_user or not m_pwd:
        raise RuntimeError(f"Cannot parse CMDS creds from {docx}")
    user = m_user.group(1)
    # docx may concatenate as "账号 xikaiwang密码：..."
    if "密码" in user:
        user = user.split("密码")[0]
    return user, m_pwd.group(1)


def get_cmds_creds() -> tuple[str, str]:
    user = os.environ.get("CMDS_USER")
    pwd = os.environ.get("CMDS_PASSWORD")
    if user and pwd:
        return user, pwd
    docx = os.environ.get("CMDS_CREDS_DOCX")
    if docx:
        return _creds_from_docx(Path(docx))
    raise RuntimeError(
        "Set CMDS_USER/CMDS_PASSWORD (or CMDS_CREDS_DOCX) before connecting"
    )


def connect(
    host: str | None = None,
    port: int | None = None,
    service: str | None = None,
    jar: Path | str | None = None,
):
    host = host or DEFAULT_DSN_HOST or os.environ.get("CMDS_HOST")
    port = port or DEFAULT_DSN_PORT or int(os.environ.get("CMDS_PORT", "0") or "0")
    service = service or DEFAULT_SERVICE
    jar = Path(jar or DEFAULT_JAR)
    if not host or not port:
        raise RuntimeError("Set CMDS_HOST and CMDS_PORT environment variables")
    if not jar.exists():
        raise FileNotFoundError(f"ojdbc jar missing: {jar}")
    user, pwd = get_cmds_creds()
    url = f"jdbc:oracle:thin:@{host}:{port}/{service}"
    return jaydebeapi.connect("oracle.jdbc.OracleDriver", url, [user, pwd], str(jar))


def read_sql(sql: str, params=None) -> pd.DataFrame:
    conn = connect()
    try:
        cur = conn.cursor()
        if params is None:
            cur.execute(sql)
        else:
            cur.execute(sql, params)
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
        cur.close()
    finally:
        conn.close()
    df = pd.DataFrame(rows, columns=cols)
    for c in df.columns:
        df[c] = df[c].map(
            lambda v: None
            if v is None
            else (float(v) if hasattr(v, "doubleValue") else v)
        )
    return df
