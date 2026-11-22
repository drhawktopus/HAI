# Lorex Camera Integration - Detection Strategy & Subject Definitions



## Design Doctrine: Priority of Motion Sources

Motion analysis prioritizes feline activity over human presence. The system is optimized to detect cat fights and litter box violations (indoor vs. outdoor elimination).

Human motion detection serves as a narrow edge case. Intruder detection is not the primary design goal; alerts trigger only for unauthorized access outside normal hours by individuals other than the owner or Avery.

## 3. The subjects

### Subject signatures for IR identification

| Name | Signature | Identifiable in IR? |
|---|---|---|
| **Lucy** | Solid black, largest | ✅ Dark + size |
| **Binks** | Black & white, young, short hair | ✅ High contrast |
| **Zeke** | Grey/black striped, medium-long hair | ✅ Silhouette |
| **Zag** | Orange tabby, short hair | ⚠ Becomes mid-grey |
| **Stella** | Calico (orange/black/white/brown), reclusive | ⚠ Becomes mixed greys |
| **Sam** | FIV, resides in basement | ✅ Covered by Ch2 camera |

### Interaction priors

Use these social patterns to contextualize detected interactions:
- Binks targets Zag.
- Zag pesters Lucy; Lucy reacts with annoyance.
- Lucy is the primary suspect for pooping incidents and is the most IR-identifiable cat.

## Camera coverage and colour identification doctrine

### Coverage geometry
Both cameras cover fixed litter box regions. Channel 1 (Dining Room) covers a tray inside the fireplace. Channel 2 (Basement) covers a blue tray at the bottom-right. This reduces object detection scope from full-frame analysis to monitoring two fixed regions.

### Colour dependency for identification
Cat identification relies on colour data. Infrared mode produces equal RGB values (R==G==B), resulting in near-zero HSV saturation and insufficient separation for most cats.

**Identification confidence by mode:**
*   **Colour mode:** Supports separation of all five cats.
*   **Infrared mode:** Supports separation of only Lucy, Binks, and Zeke.

**Design requirement:** Every logged event must carry a colour flag. This flag bounds identification confidence based on the active imaging mode.

### Colour availability measurement protocol
Colour availability is time-dependent and environment-specific. Because channel locations change (e.g., ch2 relocation), static window observations become invalid immediately upon hardware move. Do not rely on historical time windows.

*   **Protocol:** Verify colour availability via live status trackers or fresh measurement before relying on mode-based identification confidence for a specific session.
*   **Constraint:** Derived observations from previous channel placements (e.g., front door daylight interactions) are invalid after relocation.

## Pending configuration decisions
* **Channel 1 color forcing:** Current configuration uses `DayNightColor=1` (auto). Forcing colour (`DayNightColor` override) is under consideration to improve visibility for Zag/Stella in low-light conditions, though this introduces noise risk. Evaluation requires measurement before implementation.
* **Credential isolation:** A non-admin RTSP/API account must be provisioned before any persistent configuration files embed `admin` credentials.
