# Install the CUDA 13.0.2 toolkit's COMPILE pieces on the lab engine's Windows build box: nvcc, the runtime, cuBLAS
# (+ dev headers/import libs), crt, and the VS integration - no driver (the build box may have no NVIDIA card; the
# toolkit only compiles there). 13.0 = the major/minor ollama 0.40.5's cuda_v13 ships (cublas64_13).
# Log: C:\ProgramData\HAI\cuda13-install.log
$log = 'C:\ProgramData\HAI\cuda13-install.log'
function L($m) { "$(Get-Date -Format o) $m" | Out-File -FilePath $log -Append -Encoding utf8 }
$dir = 'C:\labbuild\dl'
New-Item -ItemType Directory -Force $dir | Out-Null
$exe = Join-Path $dir 'cuda_13.0.2_windows_network.exe'
$url = 'https://developer.download.nvidia.com/compute/cuda/13.0.2/network_installers/cuda_13.0.2_windows_network.exe'
try {
    L "download $url"
    Invoke-WebRequest -Uri $url -OutFile $exe -UseBasicParsing
    L ("downloaded {0:N0} bytes, sha256 {1}" -f (Get-Item $exe).Length, (Get-FileHash $exe -Algorithm SHA256).Hash)
    # nvvm (cicc, the CUDA front end nvcc calls) is listed on purpose: without it the compiler self-test fails with
    # "The system cannot find the path specified". Re-running adds what is absent.
    $pk = 'nvcc_13.0 nvvm_13.0 cudart_13.0 cublas_13.0 cublas_dev_13.0 crt_13.0 visual_studio_integration_13.0'
    L "install -s $pk"
    $p = Start-Process -FilePath $exe -ArgumentList ("-s " + $pk) -Wait -PassThru
    L "installer exit $($p.ExitCode)"
    $nvcc = Get-ChildItem 'C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA' -Recurse -Filter nvcc.exe -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($nvcc) { L ("nvcc: " + $nvcc.FullName + " :: " + ((& $nvcc.FullName --version) -join ' | ')) } else { L "nvcc NOT FOUND" }
    L "DONE"
} catch {
    L ("FAILED: " + $_)
}
