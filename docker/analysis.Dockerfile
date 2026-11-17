# Data-analysis sandbox: runs an agent-written script offline (--network none) against
# server-fetched inputs. No LLM, no credentials inside.
FROM python:3.13-slim
RUN pip install --no-cache-dir pandas==2.3.3 numpy openpyxl xlrd pyarrow matplotlib scipy statsmodels \
        tabulate chardet odfpy lxml html5lib beautifulsoup4 \
    && useradd -m -u 1001 analyst \
    && mkdir -p /usr/share/fonts/truetype
RUN apt-get update && apt-get install -y --no-install-recommends fonts-nanum && rm -rf /var/lib/apt/lists/*
USER analyst
WORKDIR /work
ENV MPLBACKEND=Agg PYTHONIOENCODING=utf-8 PYTHONDONTWRITEBYTECODE=1
