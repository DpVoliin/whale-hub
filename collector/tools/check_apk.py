#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""发版自检：这个 APK 能不能拿到**你自己的服务器**上用？

为什么要有它（真实犯过的错）：
    我把「公开仓库源码」编出来的包当私有包发给了主人 —— 公开仓库里的配置**刻意是占位符**
    （`DEFAULT_HUB=""`、`<domain>YOUR_SERVER_IP</domain>`，谁都能拿到源码、不能把你的
    IP/口令写进去），于是 App 既没有中枢地址、也认不出服务器证书 →
    上报全部失败（`java.security.cert.CertPathValidatorException: Trust anchor`），
    数据在手机上排了 125 条出不去。这类错**长得跟正常一模一样**（能装、能开、UI 正常），
    只能靠"发之前把包里那几处关键值读出来比一遍"拦住 —— 所以做成脚本，不靠记性。

用法：
    python3 collector/tools/check_apk.py /path/to/app.apk --host 你的服务器IP:11443
    （--host 省略时取环境变量 WHALE_HUB_HOST）

检查项（任一项不过就是 ✗，**别发**）：
    ① 证书固定里的域名是真实 IP（不是 YOUR_SERVER_IP 之类的占位符）
    ② 默认中枢地址是 https://<host>（不是空、不是 http）
    ③ 包内固定证书的 SHA-256 == 服务器**当前**证书（换过证书的包连不上）
    ④ 签名密钥指纹（打出来供对照：和手机上已装的必须一致，否则得先卸载）
"""
import argparse
import base64
import hashlib
import os
import pathlib
import re
import socket
import ssl
import subprocess
import sys
import zipfile

PLACEHOLDERS = ("YOUR_SERVER_IP", "your_server_ip", "example.com", "TODO", "CHANGEME")


def dex_blob(z):
    return b"".join(z.read(n) for n in z.namelist() if n.startswith("classes") and n.endswith(".dex"))


def cert_sha256_of_pem(pem_bytes):
    txt = pem_bytes.decode("utf-8", "replace")
    b64 = "".join(re.findall(r"-----BEGIN CERTIFICATE-----(.*?)-----END CERTIFICATE-----", txt, re.S))
    if not b64:
        return None
    return hashlib.sha256(base64.b64decode(b64)).hexdigest()


def live_cert_sha256(host, port):
    ctx = ssl._create_unverified_context()
    with socket.create_connection((host, port), timeout=20) as s:
        with ctx.wrap_socket(s, server_hostname=host) as ss:
            return hashlib.sha256(ss.getpeercert(binary_form=True)).hexdigest()


def signer_fingerprint(apk):
    """用 SDK 里的 apksigner 读签名指纹（没有就返回 None，不因此判失败）。"""
    sdk = os.getenv("ANDROID_HOME") or os.getenv("ANDROID_SDK_ROOT") or str(pathlib.Path.home() / "android-sdk")
    cands = sorted(pathlib.Path(sdk).glob("build-tools/*/apksigner"))
    if not cands:
        return None
    p = subprocess.run([str(cands[-1]), "verify", "--print-certs", str(apk)],
                       capture_output=True, text=True)
    m = re.search(r"Signer #1 certificate SHA-256 digest:\s*([0-9a-f]+)", p.stdout)
    return m.group(1) if m else None


def main():
    ap = argparse.ArgumentParser(description="发版自检：这个 APK 能不能用在你的服务器上")
    ap.add_argument("apk")
    ap.add_argument("--host", default=os.getenv("WHALE_HUB_HOST", ""), help="你的中枢 host:port")
    a = ap.parse_args()
    if not a.host:
        print("✗ 没给 --host（或设 WHALE_HUB_HOST）—— 不知道要拿哪个服务器比，不敢放行")
        return 2
    host, _, port = a.host.partition(":")
    port = port or "11443"
    apk = pathlib.Path(a.apk)
    if not apk.exists():
        print("✗ 文件不存在：%s" % apk)
        return 2

    z = zipfile.ZipFile(apk)
    blob = dex_blob(z)
    try:
        xb = z.read("res/xml/network_security_config.xml")
    except KeyError:
        xb = b""
    try:
        cert_pem = z.read("res/raw/hub_cert")
    except KeyError:
        cert_pem = b""

    rows, ok_all = [], True

    def row(name, good, detail, hard=True):
        nonlocal ok_all
        if hard and not good:
            ok_all = False
        rows.append((name, "✓" if good else ("✗" if hard else "·"), detail))

    # ① 证书固定里的域名
    has_ip = host.encode() in xb
    has_ph = [p for p in PLACEHOLDERS if p.encode() in xb]
    row("证书固定域名", has_ip and not has_ph,
        ("写了 %s" % host) if has_ip else ("占位符 %s ← 公开仓库版，不能私用！" % has_ph if has_ph else "没找到"))

    # ② 默认中枢地址
    https_ok = ("https://%s" % a.host).encode() in blob
    http_only = ("http://%s" % host).encode() in blob and not https_ok
    row("默认中枢地址", https_ok,
        "https://%s 已内置" % a.host if https_ok else ("只有 http（会被明文拦截）" if http_only else "空/缺失（要手填）"))

    # ③ 固定证书 == 服务器当前证书
    apk_fp = cert_sha256_of_pem(cert_pem)
    try:
        live = live_cert_sha256(host, int(port))
    except Exception as e:
        live = None
        row("服务器证书", False, "连不上 %s（%s）—— 无法比对" % (a.host, type(e).__name__), hard=False)
    if live:
        row("固定证书", apk_fp == live,
            "一致 %s…" % (apk_fp or "无")[:16] if apk_fp == live else "包内 %s… ≠ 服务器 %s…" % ((apk_fp or "无")[:12], live[:12]))

    # ④ 签名指纹（供和手机上已装版本对照）
    fp = signer_fingerprint(apk)
    row("签名指纹", True, (fp or "读不到（没装 build-tools）"), hard=False)

    print("=== 发版自检：%s ===" % apk.name)
    for name, mark, detail in rows:
        print("  %s %-14s %s" % (mark, name, detail))
    print()
    if ok_all:
        print("✓ 可以发（这三项都过了；签名指纹请与手机上已装的对照，不一致就要先卸载）")
    else:
        print("✗ **别发** —— 上面有硬项没过。多半是拿了公开仓库版：")
        print("   该包的配置是占位符，装到你手机上会「没有地址 + 认不出证书」→ 上报全失败。")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
