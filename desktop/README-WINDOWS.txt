CoSolve Lens v0.1.0 - Windows release
=======================================

Requirements
------------
- Windows 10 or 11
- Python 3.11 or newer
- Microsoft Edge or Google Chrome
- Internet access during the first dependency installation

First run
---------
1. Extract the complete ZIP. Do not run files inside the ZIP preview.
2. Double-click Install-CoSolve-Lens.cmd.
3. After installation, double-click one of:
   - 教师登录.exe
   - 学生登录.exe
   - 管理员登录.exe

The release starts in local demo mode. It contains synthetic fixtures and no
DeepSeek API key, real student database, StudyChat transcript, or browser data.

Stop the local service
----------------------
Open Command Prompt in this folder and run:

  教师登录.exe --stop

Security note
-------------
The course-project launchers are not code-signed, so Windows may show an
unknown-publisher warning. Verify the SHA-256 file published with the release
and only run packages downloaded from the official GitHub repository.
