#include <windows.h>

/* Unicode GUI launcher using only Windows system DLLs, without a C runtime. */
static WCHAR directory[32768], python[32768], command[32768];
static STARTUPINFOW startup;
static PROCESS_INFORMATION process;

void WINAPI start(void) {
    DWORD length = GetModuleFileNameW(NULL, directory, 32768), last = 0;
    if (!length || length > 32700) ExitProcess(1);
    for (DWORD i = 0; i < length; ++i) if (directory[i] == L'\\') last = i;
    if (!last) ExitProcess(1);
    directory[last] = 0;
    DWORD i = 0;
    for (; directory[i]; ++i) python[i] = directory[i];
    const WCHAR suffix[] = L"\\runtime\\pythonw.exe";
    for (DWORD j = 0; suffix[j]; ++j) python[i++] = suffix[j];
    python[i] = 0;
    command[0] = L'"';
    for (DWORD j = 0; j < i; ++j) command[j + 1] = python[j];
    const WCHAR arguments[] = L"\" -m companion";
    for (DWORD j = 0; arguments[j]; ++j) command[++i] = arguments[j];
    command[i + 1] = 0;
    startup.cb = sizeof(startup);
    SetEnvironmentVariableW(L"PYTHONUTF8", L"1");
    if (!CreateProcessW(python, command, NULL, NULL, FALSE, CREATE_NO_WINDOW, NULL, directory, &startup, &process)) {
        MessageBoxW(NULL, L"Не удалось запустить Warcraft Audi. Полностью распакуйте архив и запустите программу из распакованной папки.", L"Warcraft Audi", MB_OK | MB_ICONERROR);
        ExitProcess(1);
    }
    CloseHandle(process.hThread);
    CloseHandle(process.hProcess);
    ExitProcess(0);
}
