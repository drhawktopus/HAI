# The hwdec tests' lab settings, sourced by each script that runs on the lab's own box (not the tablet-side
# run_serve*.sh / burst_clip.sh, which take NVR and CRED from their caller): the fellowship's settings (KEY, ADB,
# TABLETS, SERIALS: fellowship/settings.sh) and the NVR's address (NVR, else NVR_HOST).
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../../fellowship/settings.sh"
eval "$(python "$LABBIN/lab_settings.py" --sh NVR_HOST)"
NVR="${NVR:-$NVR_HOST}"
