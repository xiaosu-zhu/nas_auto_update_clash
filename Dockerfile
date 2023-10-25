FROM continuumio/miniconda3:23.5.2-0

RUN pip config set global.index-url https://mirrors.aliyun.com/pypi/simple

SHELL ["/bin/bash", "-c"]

RUN echo $'channels: \n\
  - defaults \n\
show_channel_urls: true \n\
default_channels: \n\
  - http://mirrors.aliyun.com/anaconda/pkgs/main \n\
  - http://mirrors.aliyun.com/anaconda/pkgs/r \n\
  - http://mirrors.aliyun.com/anaconda/pkgs/msys2 \n\
custom_channels: \n\
  conda-forge: http://mirrors.aliyun.com/anaconda/cloud \n\
  msys2: http://mirrors.aliyun.com/anaconda/cloud \n\
  bioconda: http://mirrors.aliyun.com/anaconda/cloud \n\
  menpo: http://mirrors.aliyun.com/anaconda/cloud \n\
  pytorch: http://mirrors.aliyun.com/anaconda/cloud \n\
  simpletk: http://mirrors.aliyun.com/anaconda/cloud '  > ~/.condarc


# fastapi and rocketry need newer python
RUN conda install "python>=3.12"
RUN conda install requests joblib schedule pyyaml rocketry fastapi uvicorn-standard -c conda-forge


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
