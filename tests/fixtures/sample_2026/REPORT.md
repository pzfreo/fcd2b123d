# sample_2026 — true-random library audit

Random sample of **100** files at seed `2026` from the full 3194-file FreeCAD Parts Library coverage DB. No tier filter — out-of-scope, FeaturePython, multi-body, the lot.

## Outcome distribution

| Status | Count | % |
|---|---:|---:|
| `pass` | 65 | 65% |
| `unsupported` | 26 | 26% |
| `cached_pass` | 4 | 4% |
| `cached_excluded` | 1 | 1% |
| `exec_error` | 1 | 1% |
| `snapshot_timeout` | 1 | 1% |
| `translator_timeout` | 1 | 1% |
| `snapshot_fail` | 1 | 1% |

**Accurate translation rate: 69/100 = 69% ** (PASS or cached-PASS — geometry matches FreeCAD's BRep within the four-scalar + Hausdorff tolerances).

## Per-file results (manifest.json has the full data)

| # | source path | status |
|---:|---|---|
| 1 | `Mechanical Parts/Pulleys/GT2Pulley-V2.fcstd` | `unsupported` |
| 2 | `Metric/ISO4762 Hexagon socket head cap screws/Screw M8x160 ISO4762 8,8 A2K.FCStd` | `cached_pass` |
| 3 | ` Parts/Profiles EN/EN10059 Square steel bars/Square Bar 220 EN10059 S235JR.FCStd` | `pass` |
| 4 | `arts/Profiles EN/DIN1025-3 HE-A-Profiles/HE-A-Profile 700 DIN1025-3 S235JR.FCStd` | `pass` |
| 5 | `Electronics Parts/Connectors/female/3pin-female-2.54mm-connector.fcstd` | `unsupported` |
| 6 | `ts/Chains/Sprocket/ISO 606/Simplex ⅜x⁷⁄₃₂/Sprocket ANSI simplex ⅜x⁷⁄₃₂ z22.FCStd` | `unsupported` |
| 7 | `arts/Fasteners/Bolt Clearance Hole Cylinders/Metric/Normal/M3/M3NormalMean.FCStd` | `pass` |
| 8 | `al Parts/Profiles EN/EN10060 Round steel bars/Round Bar 190 EN10060 S235JR.FCStd` | `pass` |
| 9 | `tics/Shipping Containers/20_Feet_ISO_Container/front-member_vertical-2_mir.FCStd` | `pass` |
| 10 | `gular Hollow Sections/Rectangular hollow section 180x100x5 EN10219 S235JRH.FCStd` | `pass` |
| 11 | `cal Parts/Profiles EN/EN10058 Flat steel bars/Flat Bar 80x5 EN10058 S235JR.FCStd` | `pass` |
| 12 | `ular Hollow Sections/Rectangular hollow section 260x180x12 EN10219 S235JRH.FCStd` | `pass` |
| 13 | `arts/Profiles EN/DIN1025-2 HE-B-Profiles/HE-B-Profile 320 DIN1025-2 S235JR.FCStd` | `pass` |
| 14 | `Electrical Parts/Hotend/MK8.FCStd` | `unsupported` |
| 15 | `DummiesAndSculptures/Mannequin_mp/Mannequin_mp-dummy-1850mm-standing-007.FCStd` | `cached_excluded` |
| 16 | `Generic objects/Button_Proudly_made_by_a_Maker.fcstd` | `unsupported` |
| 17 | `219 Square Hollow Sections/Square hollow section 40x40x3.6 EN10219 S235JRH.FCStd` | `pass` |
| 18 | `Pipes and tubes/Generic siphon.FCStd` | `unsupported` |
| 19 | `Medical Parts/01 Biomedical/OpenLung/Parts/IgnusNutMount.FCStd` | `exec_error` |
| 20 | `ar Hollow Sections/Rectangular hollow section 200x120x12.5 EN10219 S235JRH.FCStd` | `pass` |
| 21 | `l Parts/Profiles EN/EN10058 Flat steel bars/Flat Bar 150x40 EN10058 S235JR.FCStd` | `pass` |
| 22 | `Parts/Fasteners/Bolt Clearance Hole Cylinders/Metric/Close/M64/M64CloseMax.FCStd` | `pass` |
| 23 | `HVAC/Ducts/Rectangular/Conections/Chamfered_rectangular_bend.FCStd` | `unsupported` |
| 24 | `Mechanical Parts/Enclosures/HJC_040/Insert_GND.FCStd` | `unsupported` |
| 25 | `chanical Parts/Chains/Sprocket/ISO 606/Duplex ⅝x⅜/Sprocket ANSI duplex ⅝x⅜.FCStd` | `unsupported` |
| 26 | `ts/Chains/Sprocket/ISO 606/Simplex 8x3,0/Sprocket ISO606 simplex 8x3,0 z57.FCStd` | `snapshot_timeout` |
| 27 | `Metric/ISO4762 Hexagon socket head cap screws/Screw M10x70 ISO4762 8,8 A2K.FCStd` | `pass` |
| 28 | `ts/Chains/Sprocket/ISO 606/Simplex ⅜x⁷⁄₃₂/Sprocket ANSI simplex ⅜x⁷⁄₃₂ z31.FCStd` | `unsupported` |
| 29 | `al Parts/Profiles EN/EN10058 Flat steel bars/Flat Bar 25x20 EN10058 S235JR.FCStd` | `pass` |
| 30 | `ts/Chains/Sprocket/ISO 606/Simplex 6x2,8/Sprocket ISO606 simplex 6x2,8 z26.FCStd` | `pass` |
| 31 | `Electronics Parts/Headers/2.54mm-pitch/female/2x18-female-pin-header.fcstd` | `unsupported` |
| 32 | `219 Square Hollow Sections/Square hollow section 350x350x8 EN10219 S235JRH.FCStd` | `pass` |
| 33 | `cs Parts/Headers/2.54mm-pitch/male/box/2x5-pin-box-header-male-right-angle.fcstd` | `unsupported` |
| 34 | `etric/ISO4762 Hexagon socket head cap screws/Screw M12x240 ISO4762 8,8 A2K.FCStd` | `pass` |
| 35 | `/Bolt Clearance Hole Cylinders/Unified/Loose/U_Number_4/U_Number_4LooseMin.FCStd` | `pass` |
| 36 | `/Profiles EN/EN10056 Unequal Angle Bars/Angle Bar 130x90x10 EN10056 S235JR.FCStd` | `pass` |
| 37 | `l Parts/Fasteners/Bolt Clearance Hole Cylinders/Metric/Close/M4/M4CloseMax.FCStd` | `pass` |
| 38 | `etric/ISO4762 Hexagon socket head cap screws/Screw M10x180 ISO4762 8,8 A2K.FCStd` | `pass` |
| 39 | `Metric/ISO4762 Hexagon socket head cap screws/Screw M4x110 ISO4762 8,8 A2K.FCStd` | `pass` |
| 40 | `s/Metric/ISO4762 Hexagon socket head cap screws/Screw M2x8 ISO4762 8,8 A2K.FCStd` | `pass` |
| 41 | `Electrical Parts/fans/blower-50x50mm.fcstd` | `unsupported` |
| 42 | `arts/Profiles EN/DIN1025-3 HE-A-Profiles/HE-A-Profile 260 DIN1025-3 S235JR.FCStd` | `pass` |
| 43 | `Electronics Parts/Semiconductors/TO92_3.81.FCStd` | `unsupported` |
| 44 | `ts/Chains/Sprocket/ISO 606/Simplex ¾x⁷⁄₁₆/Sprocket ANSI simplex ¾x⁷⁄₁₆ z10.FCStd` | `unsupported` |
| 45 | `Industrial Design/Tables/ComputerDesk (100 x 50 x 75 cm WDH).FCStd` | `pass` |
| 46 | `Metric/ISO4762 Hexagon socket head cap screws/Screw M20x85 ISO4762 8,8 A2K.FCStd` | `cached_pass` |
| 47 | `al Parts/Chains/Sprocket/ISO 606/Simplex ½x⅛/Sprocket ANSI simplex ½x⅛ z38.FCStd` | `unsupported` |
| 48 | `ngular Hollow Sections/Rectangular hollow section 120x80x8 EN10219 S235JRH.FCStd` | `pass` |
| 49 | `arts/Profiles EN/DIN1025-4 HE-M-Profiles/HE-M-Profile 200 DIN1025-4 S235JR.FCStd` | `pass` |
| 50 | `/Metric/ISO4762 Hexagon socket head cap screws/Screw M6x40 ISO4762 8,8 A2K.FCStd` | `pass` |
| 51 | `etric/ISO4762 Hexagon socket head cap screws/Screw M10x300 ISO4762 8,8 A2K.FCStd` | `pass` |
| 52 | `l Parts/Profiles EN/EN10058 Flat steel bars/Flat Bar 110x25 EN10058 S235JR.FCStd` | `pass` |
| 53 | `Mechanical Parts/Fasteners/Washers/Metric/DIN463_M14TabWasher.fcstd` | `pass` |
| 54 | `Electronics Parts/Connectors/power-connectors/housing-2-54mm-2p.fcstd` | `pass` |
| 55 | `Architectural Parts/Symbols/People symbols/Man01.FCStd` | `unsupported` |
| 56 | `Architectural Parts/Construction blocks/Canal block.FCStd` | `cached_pass` |
| 57 | `lar Hollow Sections/Rectangular hollow section 200x120x6.3 EN10219 S235JRH.FCStd` | `pass` |
| 58 | `Metric/ISO4762 Hexagon socket head cap screws/Screw M5x170 ISO4762 8,8 A2K.FCStd` | `pass` |
| 59 | `ts/Chains/Sprocket/ISO 606/Simplex ½x⁵⁄₁₆/Sprocket ANSI simplex ½x⁵⁄₁₆ z57.FCStd` | `unsupported` |
| 60 | `Architectural Parts/Windows/Glass-skin/6 frame modules.FCStd` | `unsupported` |
| 61 | `Metric/ISO4762 Hexagon socket head cap screws/Screw M5x140 ISO4762 8,8 A2K.FCStd` | `pass` |
| 62 | `etric/ISO4762 Hexagon socket head cap screws/Screw M16x240 ISO4762 8,8 A2K.FCStd` | `pass` |
| 63 | `19 Square Hollow Sections/Square hollow section 400x400x16 EN10219 S235JRH.FCStd` | `pass` |
| 64 | `cal Parts/Profiles EN/EN10058 Flat steel bars/Flat Bar 70x5 EN10058 S235JR.FCStd` | `pass` |
| 65 | `al Parts/Chains/Sprocket/ISO 606/Simplex ½x⅛/Sprocket ANSI simplex ½x⅛ z23.FCStd` | `unsupported` |
| 66 | `ts/Profiles EN/EN10056 Unequal Angle Bars/Angle Bar 45x30x3 EN10056 S235JR.FCStd` | `pass` |
| 67 | `ar Hollow Sections/Rectangular hollow section 250 x150x6.3 EN10219 S235JRH.FCStd` | `pass` |
| 68 | `ular Hollow Sections/Rectangular hollow section 500x300x20 EN10219 S235JRH.FCStd` | `cached_pass` |
| 69 | `l Parts/Profiles EN/EN10059 Square steel bars/Square Bar 50 EN10059 S235JR.FCStd` | `pass` |
| 70 | `ts/Chains/Sprocket/ISO 606/Simplex 8x3,0/Sprocket ISO606 simplex 8x3,0 z15.FCStd` | `pass` |
| 71 | `al Parts/Profiles EN/EN10058 Flat steel bars/Flat Bar 70x30 EN10058 S235JR.FCStd` | `pass` |
| 72 | ` Parts/Profiles EN/DIN1026-2 UPE-Profiles/UPE-Profile 120 DIN1026-2 S235JR.FCStd` | `pass` |
| 73 | `Pipes and tubes/DN15_FIG_130.FCStd` | `unsupported` |
| 74 | `al Parts/Profiles EN/EN10060 Round steel bars/Round Bar 110 EN10060 S235JR.FCStd` | `pass` |
| 75 | `10219 Square Hollow Sections/Square hollow section 60x60x3 EN10219 S235JRH.FCStd` | `pass` |
| 76 | `ts/Profiles EN/EN10056 Unequal Angle Bars/Angle Bar 70x50x5 EN10056 S235JR.FCStd` | `pass` |
| 77 | `rts/Profiles EN/EN10056 Equal Angle Bars/Angle Bar L70x70x7 EN10056 S235JR.FCStd` | `pass` |
| 78 | ` Parts/Fasteners/Bolt Clearance Hole Cylinders/Metric/Loose/M3/M3LooseMean.FCStd` | `pass` |
| 79 | `cal Parts/Profiles EN/EN10058 Flat steel bars/Flat Bar 60x4 EN10058 S235JR.FCStd` | `pass` |
| 80 | `lar Hollow Sections/Rectangular hollow section 300x200x6.3 EN10219 S235JRH.FCStd` | `pass` |
| 81 | `9 Square Hollow Sections/Square hollow section 100x100x6.3 EN10219 S235JRH.FCStd` | `pass` |
| 82 | `arts/Profiles EN/DIN1025-3 HE-A-Profiles/HE-A-Profile 550 DIN1025-3 S235JR.FCStd` | `pass` |
| 83 | `rts/Profiles EN/EN10056 Equal Angle Bars/Angle Bar L35x35x3 EN10056 S235JR.FCStd` | `pass` |
| 84 | `ts/Chains/Sprocket/ISO 606/Simplex ½x⁵⁄₁₆/Sprocket ANSI simplex ½x⁵⁄₁₆ z36.FCStd` | `unsupported` |
| 85 | `gular Hollow Sections/Rectangular hollow section 250x150x5 EN10219 S235JRH.FCStd` | `pass` |
| 86 | `ts/Fasteners/Bolt Clearance Hole Cylinders/Metric/Normal/M48/M48NormalMean.FCStd` | `pass` |
| 87 | `Generic objects/Foundation/Caisson.FCStd` | `pass` |
| 88 | `Mechanical Parts/Mountings/T8_leadscrew/T8_leadscrew_150mm.FCStd` | `translator_timeout` |
| 89 | ` Parts/Chains/Sprocket/ISO 606/Simplex 2x1¼/Sprocket ANSI simplex 2x1¼ z17.FCStd` | `unsupported` |
| 90 | `Architectural Parts/Symbols/People symbols/Man06.FCStd` | `unsupported` |
| 91 | `/Profiles EN/EN10056 Equal Angle Bars/Angle Bar L140x140x10 EN10056 S235JR.FCStd` | `pass` |
| 92 | `nics Parts/Motors/Stepper/NEMA/Nema-17_Mount_Bracket/Nema-17_Mount_Bracket.fcstd` | `unsupported` |
| 93 | `Architectural Parts/Kitchen/Kitchen_cabinet_sink.FCStd` | `snapshot_fail` |
| 94 | `Logistics/Shipping Containers/20_Feet_ISO_Container/front-member_vertical.FCStd` | `pass` |
| 95 | `al Parts/Profiles EN/EN10058 Flat steel bars/Flat Bar 70x50 EN10058 S235JR.FCStd` | `pass` |
| 96 | `19 Square Hollow Sections/Square hollow section 200x200x12 EN10219 S235JRH.FCStd` | `pass` |
| 97 | `hanical Parts/Fasteners/Washers/Metric/ISO7091DIN126_CLASS_C_M18FlatWasher.fcstd` | `pass` |
| 98 | `19 Square Hollow Sections/Square hollow section 250x250x16 EN10219 S235JRH.FCStd` | `pass` |
| 99 | `Parts/Fasteners/Bolt Clearance Hole Cylinders/Metric/Normal/M8/M8NormalMax.FCStd` | `pass` |
| 100 | `echanical Parts/Bearings/parametric_axial_bearing/parametric_axial_bearing.fcstd` | `unsupported` |
