FROM public.ecr.aws/lambda/python:3.9

# 安装系统依赖
RUN yum update -y && \
    yum install -y \
        gcc \
        python3-devel \
        freetype-devel \
        libpng-devel \
    && yum clean all

# 复制并安装Python依赖
COPY requirements.txt ${LAMBDA_TASK_ROOT}
RUN pip install --no-cache-dir -r requirements.txt

# 复制Lambda函数代码
COPY lambda_function.py ${LAMBDA_TASK_ROOT}

# 设置Lambda处理器
CMD ["lambda_function.lambda_handler"]