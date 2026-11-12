$ErrorActionPreference = 'Stop'
& .\.venv\Scripts\python.exe -m unittest discover -s tests -v
if ($LASTEXITCODE) { exit $LASTEXITCODE }
# Run with a HOST clang in PATH. ESP-Clang defaults to RISC-V and is unsuitable.
New-Item -ItemType Directory -Force artifacts | Out-Null
clang -Ifirmware/main tests/test_firmware.c firmware/main/multipart.c firmware/main/viewer_state.c -o artifacts/test_firmware.exe
if ($LASTEXITCODE) { exit $LASTEXITCODE }
& .\artifacts\test_firmware.exe
exit $LASTEXITCODE
