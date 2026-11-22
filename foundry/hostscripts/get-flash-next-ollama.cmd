@echo off
rem Downloads ollama's qwen3.8-flash-next:125b-a6b-q4_K_M whole into FLASHNEXT_OLLAMA_ROOT (ollama store layout);
rem linking it into the model store is a separate step. Log: download.log in that folder.
python "%~dp0get_flash_next_ollama.py"
