' Launch the Gmail agent with no console window.
'
' The scheduled task used to call .venv\Scripts\pythonw.exe directly. The "w"
' normally means windowless, but in a uv-created venv that file is a trampoline
' compiled as a CONSOLE application, so Windows opened a console on every run -
' every two hours, stealing focus. Worse, closing that window killed the run:
' the task was recording 0xC000013A (STATUS_CONTROL_C_EXIT).
'
' wscript.exe is a GUI-subsystem program, and Run(..., 0, False) starts the
' child hidden, so nothing is ever drawn. Fixing it here rather than in the
' task's principal means no administrator rights are needed.
Option Explicit

Dim shell, root, cmd
Set shell = CreateObject("WScript.Shell")

root = "C:\Users\sbatu\Desktop\Cracker"
cmd  = """" & root & "\.venv\Scripts\pythonw.exe"" -m agent --quiet --days 120"

shell.CurrentDirectory = root
' 0 = hidden window. Wait for the agent and hand its exit code back, so Task
' Scheduler still reports a real result and its 15-minute execution limit still
' applies; launching and returning immediately would make every run look like a
' success and leave a hung agent with nothing to stop it.
WScript.Quit shell.Run(cmd, 0, True)
