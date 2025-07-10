FROM public.ecr.aws/lambda/python:3.12-x86_64
COPY requirements.txt /tmp/
RUN pip install -r /tmp/requirements.txt
COPY *.py  ${LAMBDA_TASK_ROOT}
CMD ["main.handler"]