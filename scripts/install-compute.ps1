$ErrorActionPreference='Stop'
$root=Split-Path $PSScriptRoot -Parent
Set-Location $root
if(-not (Get-Command nvidia-smi.exe -ErrorAction SilentlyContinue)){throw 'NVIDIA driver is required for GPU compute'}
if(-not (Get-Command ffmpeg.exe -ErrorAction SilentlyContinue)){throw 'FFmpeg is required'}
$python=Join-Path $root '.venv-compute\Scripts\python.exe'
if(-not (Test-Path $python)){
    & (Join-Path $root '.venv-worker\Scripts\python.exe') -m venv .venv-compute
    if($LASTEXITCODE -ne 0){throw 'Cannot create compute environment'}
}
& $python -m pip install --disable-pip-version-check torch==2.8.0 --index-url https://download.pytorch.org/whl/cu126
if($LASTEXITCODE -ne 0){throw 'CUDA PyTorch installation failed'}
& $python -m pip install --disable-pip-version-check -r (Join-Path $root 'worker\compute-requirements.txt')
if($LASTEXITCODE -ne 0){throw 'Speech dependencies installation failed'}
& $python -c 'import torch; assert torch.cuda.is_available(), "CUDA is unavailable"; print("Compute ready:",torch.cuda.get_device_name(0))'
if($LASTEXITCODE -ne 0){throw 'GPU compute verification failed'}
