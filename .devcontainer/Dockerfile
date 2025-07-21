FROM mcr.microsoft.com/devcontainers/miniconda:1-3

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
    && rm -rf /var/lib/apt/lists/*

RUN conda install -n base -c conda-forge mamba \
    && conda clean -afy

COPY environment.yml /tmp/conda-tmp/environment.yml

USER vscode

RUN mamba env create -n dev -f /tmp/conda-tmp/environment.yml \
 && conda clean -afy

USER root
RUN rm -rf /tmp/conda-tmp
