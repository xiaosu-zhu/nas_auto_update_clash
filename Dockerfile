FROM python:3.12

RUN pip config set global.index-url https://mirrors.aliyun.com/pypi/simple

RUN pip install requests joblib schedule pyyaml rocketry fastapi "uvicorn[standard]"


VOLUME ["/root/config"]


ENV CLASH_CONTROLLER_ROOT="http://127.0.0.1:9090"
ENV CLASH_CONFIG_PATH="/root/.config/clash/config.yaml"
ENV SELF_CONFIG_PATH="/root/config/config.yaml"
ENV MANAGED_CONFIG_URL="http://127.0.0.1"
ENV VERBOSE="0"
ENV CLASH_SECRET="clash_secret"


WORKDIR /root

RUN git clone https://github.com/xiaosu-zhu/nas_auto_update_clash.git

ENTRYPOINT ["python", "nas_auto_update_clash/nas_guardian.py"]
