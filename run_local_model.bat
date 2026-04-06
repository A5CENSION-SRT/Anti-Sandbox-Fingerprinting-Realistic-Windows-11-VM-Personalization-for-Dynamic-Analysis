@echo off
echo ==================================================
echo Starting Local Gemma API Server (llama.cpp)
echo Model: model\gemma-q4.gguf
echo Endpoint: http://localhost:1234/v1/completions
echo ==================================================

:: If you have llama-server.exe on your path, this will automatically boot it up!
llama-server.exe -m "model\gemma-q4.gguf" --port 1234 --ctx-size 2048
pause
