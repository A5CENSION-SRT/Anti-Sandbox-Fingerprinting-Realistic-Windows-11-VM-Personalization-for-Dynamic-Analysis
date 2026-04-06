# ARC Project Progress Report

````carousel
# Slide 1: Introduction to ARC
### Artifact Generation Framework
ARC is a synthetic data generation project focused on creating highly realistic, pre-populated Windows VHD environments for forensic training, testing, and AI research. 

Instead of deploying a virtual machine and clicking around manually, ARC programmatically writes filesystem, registry, event log, browser, and application artifacts directly into a virgin disk image — matching exact personas (e.g., developer, office worker).

<!-- slide -->
# Slide 2: Infrastructure & Orchestration
### The Core Engine

The foundation of the project is fully functional and stable:
- **Profile-Driven Execution**: Uses predefined YAML profiles (Developer, Office, etc.) to seed realistic context (usernames, computer names, install dates).
- **VHD Building Pipeline**: `build_vm_image.py` combined with Python orchestrator orchestrates 30+ microservices in logical phases.
- **Offline Writes**: Direct-to-disk artifact placement avoiding Windows API dependencies, allowing the pipeline to generate gigabytes of structured artifacts in under 5 seconds.
- **Seed Hives Construction**: Automatically lays down foundational Registry hives (`SOFTWARE`, `SYSTEM`, `SAM`, `SECURITY`, `DEFAULT`, `NTUSER.DAT`) on empty disks.

<!-- slide -->
# Slide 3: Filesystem Simulation Achievements
### Realistic MFT & Directories

We've successfully spoofed a complete Windows filesystem skeleton:
- **System Folders**: `Program Files`, `ProgramData`, `SysWOW64`, AppData, etc. 
- **Application Stubs (`InstalledAppsStub`)**: Complete PE executables (valid headers), DLLs, and configuration files for 12+ apps (Chrome, Visual Studio Code, Git, Docker, VLC) and 30+ core Windows binaries (explorer.exe, svchost.exe).
- **Core Triage Artifacts**: 
  - Prefetch (.pf files with correct run counts)
  - Thumbnail Caches (thumbcache_*.db)
  - Windows Recycle Bin ($I and $R files matching realistic byte sizes)
- **Document Generation**: Context-aware documents (Office docs, Code, Scripts) customized directly to the user's pseudo-persona.

<!-- slide -->
# Slide 4: Registry Simulation Success
### Context-Aware Hive Injection

Registry spoofing relies on `regipy` to generate high-fidelity offline hives:
- **Graceful Fault Tolerance**: Handled the transition from patching existing Windows installations to bare-metal VHD generation without crashing when parent keys are absent.
- **`InstalledPrograms`**: Populates `Uninstall` keys structurally identical to standard MSIs (UUID subkeys, InstallLocation, sizes).
- **Tracking Artifacts**: MruRecentDocs, UserAssist, NetworkProfiles successfully mapped and seeded matching the time-offsets of the active persona profile.

<!-- slide -->
# Slide 5: Browser Artifact Spoofing
### Deep Fake Web Activity

Successfully implemented comprehensive SQLite/JSON generation for Chromium-based browsers (Google Chrome & Microsoft Edge):
- **Browsing History**: Injects targeted URLs, title metadata, and visit times mapped to specific categorical topics via local datasets.
- **Cookies & Sessions**: Generates authentic-looking cached session tokens mimicking actual web application authentication flows.
- **Bookmarks**: Seeded based on persona (e.g., GitHub and Docker docs for Developer profile).
- **Downloads**: Spoofs browser download tracking tables aligning with actual files dropped into the virtual `Downloads` folder.

<!-- slide -->
# Slide 6: System & Applicative Logging
### EVTX & Forensic Traces

Standard logging systems have been replicated natively:
- **Event Logs (`EvtxWriter`)**: Capable of placing structural records into `Application.evtx`, `Security.evtx`, and `System.evtx`. 
- **Boot/Install Timestamps**: Cohesion across log entries ensuring `install_date` and `boot_time` logically align with registry hive initialization parameters. 
- **Application Layer**: Specific developer footprints like VS Code `.json` settings, Docker configurations, `.ssh/known_hosts`, and `.gitconfig` initialized.

<!-- slide -->
# Slide 7: Current Status
### Stable Generation Milestone 

The project has achieved a remarkable milestone in backend generation speed and stability:
- **Pass Rate**: 31 out of 31 active services execute successfully.
- **Speed**: Full VHD artifact generation completes in ~3-5 seconds.
- **Directory Completeness**: 320+ critical system and user entries correctly materialized in the VHD root structure natively.
- **Anti-Fingerprinting**: Operations that require Windows native execution bounds are gracefully captured into audit logs preventing fatal offline generation faults.

<!-- slide -->
# Slide 8: What Is Left - The Validation Phase
### Transitioning to Testing

We are now actively entering the **Forensic Testing Phase**. The goal is simulating real incident response workflows to verify the artifacts "trick" standard industry tooling:
- **RegRipper Validation**: We must parse the generated `NTUSER.DAT` and `SOFTWARE` hives against standard RegRipper plugins to ensure data structures (endianness, padding, unallocated space) are legitimate.
- **Timeline Analysis**: Verifying the VHD against log2timeline / Plaso to ensure there are no time-stomping anomalies or logic faults (like a file being executed before it was downloaded).
- **KAPE / Autopsy**: Pulling standard triage packages from the VHD to test parsing continuity.

<!-- slide -->
# Slide 9: Pending Bug Hunts & Consistency Checks
### Addressing Identifier Nuances

As forensic tools run, we expect to iteratively fix deep-layered validation errors:
- **GUID Cohesion**: Ensuring Machine GUIDs, Network Profile UUIDs, and Application Subkeys consistently match across Registry, EventLogs, and Prefetch references.
- **Binary Structure Polishing**: While our application `.exe` stubs have valid PE headers, deep memory carving or signature-based tools might require localized resource-section spoofing.
- **Compound Files Consistency**: Validating OLE structures in auto-generated Word/Excel documents to ensure metadata perfectly aligns with the filesystem attributes.

<!-- slide -->
# Slide 10: Future Roadmap & Enhancements
### Expanding ARC's Reach

Once forensic tool compliance is fully achieved, ARC will scale further:
- **Ransomware & Malware Artifacts**: Integrating "Red Team" persona profiles that automatically lay down Cobalt Strike, staging behavior, and ransomware encryption artifacts.
- **Memory Forensics Integration**: Correlating VHD files with synthesized memory dumps (raw physical memory faking) for Volatility parsing.
- **Continuous Integration Pipeline**: Automatically running KAPE and RegRipper over generated VHD artifacts on every pull request to guarantee forensic stability over time.

<!-- slide -->
# Appendix: Execution Commands
### Generating and Verifying the VHD

To create the VHD, populate it with artifacts, and verify the output, run the following commands from the project root (`d:\German Project\arc`):

**1. Create and Populate the VHD:**
Run the orchestrator to generate a fresh VHD and populate it with 31 microservice artifacts (e.g., developer profile) mounted at `Z:\`:
```powershell
python main.py --output "Z:\" --profile developer
```

**2. Verify VHD Population (Generate Listing):**
To see the complete tree of the populated VHD (showing all created files, registries, and logs):
```powershell
# Generates a text file containing the complete VHD directory tree
cmd /c "tree Z:\ /F /A" > images\vhd_listing.txt
```

**3. Unmount the VHD:**
When finished, eject the `Z:\` drive via Windows Explorer (Right Click -> Eject) or via Diskpart so the `.vhd` file can be safely distributed or analyzed.
````
