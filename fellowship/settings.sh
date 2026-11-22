# The fellowship scripts' lab settings, for a script to source:
#   FS       this folder, the fellowship tree (drive-letter form under Git Bash, which adb.exe needs)
#   KEY      the tablets' SSH key (TABLET_SSH_KEY)
#   ADB      adb (ADB, else ADB_EXE, else adb on the PATH)
#   TABLETS  the Fellowship's network tablets by name, SERIALS their adb serials (the FELLOWSHIP_ROSTER file)
FS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && { pwd -W 2>/dev/null || pwd; })"
LABBIN=""; for d in "$FS/../bin" "$FS/../tools"; do [ -f "$d/lab_settings.py" ] && { LABBIN="$d"; break; }; done
[ -n "$LABBIN" ] || { echo "ABORT: no lab_settings.py in $FS/../bin or $FS/../tools"; exit 1; }
eval "$(python "$LABBIN/lab_settings.py" --sh TABLET_SSH_KEY:posix ADB_EXE)"
KEY="$TABLET_SSH_KEY"
ADB="${ADB:-${ADB_EXE:-adb}}"
declare -A TABLETS SERIALS
eval "TABLETS=( $(python "$FS/roster.py" --sh-lan) )"
eval "SERIALS=( $(python "$FS/roster.py" --sh-serials) )"
