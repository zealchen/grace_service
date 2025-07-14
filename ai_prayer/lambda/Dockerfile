FROM public.ecr.aws/lambda/python:3.12-x86_64
COPY requirements.txt /tmp/
RUN dnf install -y tar xz
RUN curl -L -o ffmpeg-release.tar.xz https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz && \
    tar -xf ffmpeg-release.tar.xz && \
    cp ffmpeg-*-static/ffmpeg /usr/local/bin/ && \
    cp ffmpeg-*-static/ffprobe /usr/local/bin/ && \
    chmod +x /usr/local/bin/ffmpeg /usr/local/bin/ffprobe && \
    rm -rf ffmpeg-release.tar.xz ffmpeg-*-static
RUN pip install -r /tmp/requirements.txt
COPY *.py  ${LAMBDA_TASK_ROOT}
COPY bg.mp3 ${LAMBDA_TASK_ROOT}
CMD ["main.handler"]