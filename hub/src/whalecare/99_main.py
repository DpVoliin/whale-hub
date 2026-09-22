def main():
    init_db()
    ensure_fts()          # ★ 派生索引：建/自愈重建，失败不影响主流程
    apply_persona_pack()          # ★ 人设包：把 personas/<id>/persona.json 合并进 CFG["persona"]
    threading.Thread(target=scheduler, daemon=True).start()
    threading.Thread(target=ext_loop, daemon=True).start()   # 外挂扩展（无扩展时几乎零开销）
    port = int(CFG["port"])
    ensure_mcu_token()          # ⑧ 没有独立单片机 token 就生成一个（写回 hub.json）
    _t = CFG["token"]
    # 启动日志里把 token 打码（日志有可能被人看到、被 CI 收集）
    print(f"=== hub v{VERSION} 启动 === 端口 {port}  token {_t[:4]}…{_t[-3:]}（完整值在 hub.json）", flush=True)

    # ① HTTPS（上传用这条）：自签证书 + App 端证书固定
    tls = CFG.get("tls") or {}
    cert = os.path.join(BASE, tls.get("cert", "tls/hub.crt"))
    key = os.path.join(BASE, tls.get("key", "tls/hub.key"))
    if os.path.isfile(cert) and os.path.isfile(key):
        try:
            tls_port = int(tls.get("port", 11443))
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(cert, key)
            ctx.minimum_version = ssl.TLSVersion.TLSv1_2
            srv = ThreadingHTTPServer(("0.0.0.0", tls_port), Handler)
            srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            print(f"[hub] HTTPS {tls_port} 已开（自签证书，App 端固定）", flush=True)
        except Exception as e:
            print(f"[hub] HTTPS 起不来：{str(e)[:90]}", flush=True)
    else:
        print("[hub] 没找到证书 → 只有 HTTP（上传仍是明文）", flush=True)

    # ② HTTP（状态页/浏览器/本机自测用）
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
