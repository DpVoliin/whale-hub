# 中枢的容器镜像：单文件零依赖，所以镜像也极简
FROM python:3.12-slim

# 零第三方依赖 ⇒ 不装任何 pip 包（这一行都用不上：RUN pip install ... ）
WORKDIR /opt/whale

# 只拷中枢本体 + 工具（不拷数据库、不拷配置）
COPY hub/hub.py ./hub/hub.py
COPY hub/hubctl.py ./hub/hubctl.py
COPY hub/tools/ ./hub/tools/

# 数据目录（挂载出来，容器删了数据还在）——这是本地优先的关键
VOLUME ["/data"]
ENV WHALE_HOME=/data

# 11440 明文（内网）· 11443 HTTPS（对外）
EXPOSE 11440 11443

# 首次启动会自己生成 hub.json（含 token）与 hub.db；不给默认口令
CMD ["python3", "hub/hub.py"]
