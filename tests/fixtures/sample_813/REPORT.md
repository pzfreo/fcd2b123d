# sample_813 — true-random library audit

Random sample of **100** files at seed `813` from the full 3194-file FreeCAD Parts Library coverage DB. No tier filter — out-of-scope, FeaturePython, multi-body, the lot.

## Outcome distribution

| Status | Count | % |
|---|---:|---:|
| `pass` | 60 | 60% |
| `unsupported` | 22 | 22% |
| `snapshot_fail` | 11 | 11% |
| `props_mismatch` | 3 | 3% |
| `cached_pass` | 2 | 2% |
| `cached_excluded` | 1 | 1% |
| `translator_timeout` | 1 | 1% |

**Accurate translation rate: 62/100 = 62% ** (PASS or cached-PASS — geometry matches FreeCAD's BRep within the four-scalar + Hausdorff tolerances).

## Per-file results (manifest.json has the full data)

| # | source path | status |
|---:|---|---|
| 1 | `Mechanical Parts/Bearings/linear_bearings/LinearSlide-MGNx-XX-Rail.FCStd` | `cached_excluded` |
| 2 | `ts/Chains/Sprocket/ISO 606/Simplex ½x⁵⁄₁₆/Sprocket ANSI simplex ½x⁵⁄₁₆ z18.FCStd` | `unsupported` |
| 3 | `ical Parts/Profiles EN/EN10060 Round steel bars/Round Bar 8 EN10060 S235JR.FCStd` | `pass` |
| 4 | `Electrical Parts/Servos/SpringRC-SM-S4303R/SM-S4303R-2-arms-small-horn.fcstd` | `unsupported` |
| 5 | ` Parts/Profiles EN/DIN1025-5 IPE-Profiles/IPE-Profile 140 DIN1025-5 S235JR.FCStd` | `pass` |
| 6 | `Mechanical Parts/Profiles EN/Generic/Profile-40x40L-I-Type_Slot8.fcstd` | `pass` |
| 7 | `Mechanical Parts/Profiles EN/Generic/T-slot_20x20_90_joint.FCStd` | `unsupported` |
| 8 | `Parts/Fasteners/Bolt Clearance Hole Cylinders/Metric/Loose/M10/M10LooseMin.FCStd` | `pass` |
| 9 | `Mechanical Parts/Fasteners/Washers/Metric/ISO7093DIN9021_M24FlatWasher.fcstd` | `pass` |
| 10 | `ts/Chains/Sprocket/ISO 606/Simplex 6x2,8/Sprocket ISO606 simplex 6x2,8 z30.FCStd` | `pass` |
| 11 | `arts/Profiles EN/DIN1025-4 HE-M-Profiles/HE-M-Profile 220 DIN1025-4 S235JR.FCStd` | `pass` |
| 12 | `Metric/ISO4762 Hexagon socket head cap screws/Screw M1,6x8 ISO4762 8,8 A2K.FCStd` | `pass` |
| 13 | `ts/Chains/Sprocket/ISO 606/Simplex ½x⁵⁄₁₆/Sprocket ANSI simplex ½x⁵⁄₁₆ z39.FCStd` | `unsupported` |
| 14 | `ts/Profiles EN/EN10056 Unequal Angle Bars/Angle Bar 60x40x5 EN10056 S235JR.FCStd` | `pass` |
| 15 | `l Parts/Fasteners/Bolt Clearance Hole Cylinders/Metric/Close/M5/M5CloseMin.FCStd` | `cached_pass` |
| 16 | `ectural Parts/Windows/Sliding/Sliding window and two fixed vertical sheets.FCStd` | `snapshot_fail` |
| 17 | `Metric/ISO4762 Hexagon socket head cap screws/Screw M16x90 ISO4762 8,8 A2K.FCStd` | `pass` |
| 18 | `al Parts/Chains/Sprocket/ISO 606/Simplex ½x¼/Sprocket ANSI simplex ½x¼ z37.FCStd` | `unsupported` |
| 19 | `etric/ISO4762 Hexagon socket head cap screws/Screw M20x280 ISO4762 8,8 A2K.FCStd` | `pass` |
| 20 | ` Parts/Fasteners/Bolt Clearance Hole Cylinders/Metric/Loose/M6/M6LooseMean.FCStd` | `pass` |
| 21 | ` Parts/Chains/Sprocket/ISO 606/Simplex ¼x⅛/Sprocket ISO606 simplex ¼x⅛ z40.FCStd` | `unsupported` |
| 22 | `Parts/Fasteners/Bolt Clearance Hole Cylinders/Metric/Close/M56/M56CloseMax.FCStd` | `pass` |
| 23 | `Electronics Parts/Semiconductors/TO92_clear.FCStd` | `snapshot_fail` |
| 24 | `Electronics Parts/Boards/Arduino/Arduino UNO/arduinounomissmetal.FCStd` | `props_mismatch` |
| 25 | `rts/Fasteners/Bolt Clearance Hole Cylinders/Metric/Normal/M30/M30NormalMax.FCStd` | `pass` |
| 26 | `Mechanical Parts/Profiles EN/Makerbeam/makerbeam_bracket_corner.FCStd` | `pass` |
| 27 | `Mechanical Parts/Fasteners/Washers/Metric/DIN440_CLASS_V_M5.fcstd` | `pass` |
| 28 | `Parts/Chains/Plate Wheel/ISO 606/Simplex ⅜x⁷⁄₃₂/Plate Wheel simplex ⅜x⁷⁄₃₂.FCStd` | `unsupported` |
| 29 | `l Parts/Fasteners/Bolt Clearance Hole Cylinders/Metric/Loose/M6/M6LooseMin.FCStd` | `pass` |
| 30 | `al Parts/Profiles EN/EN10058 Flat steel bars/Flat Bar 110x8 EN10058 S235JR.FCStd` | `pass` |
| 31 | `ts/Chains/Sprocket/ISO 606/Simplex 5x2,5/Sprocket ISO606 simplex 5x2,5 z29.FCStd` | `pass` |
| 32 | `ts/Chains/Sprocket/ISO 606/Simplex ½x⁵⁄₁₆/Sprocket ANSI simplex ½x⁵⁄₁₆ z10.FCStd` | `unsupported` |
| 33 | `Electronics Parts/Headers/2.54mm-pitch/female/1x10-female-pin-header.fcstd` | `snapshot_fail` |
| 34 | `Electrical Parts/Winch/Winch-Model1-Parts/Winch-Model1-Motor-Cable-Coil.fcstd` | `pass` |
| 35 | `Industrial Design/Shelf/Batman shelf.FCStd` | `snapshot_fail` |
| 36 | `Architectural Parts/Roof/steel-sheets-3000mm.fcstd` | `props_mismatch` |
| 37 | `ro equipment/Faucets/Faucet_Solone LOP4-B043-21/Faucet_Solone LOP4-B043-21.FCStd` | `snapshot_fail` |
| 38 | `10219 Square Hollow Sections/Square hollow section 80x80x6 EN10219 S235JRH.FCStd` | `cached_pass` |
| 39 | `al Parts/Chains/Sprocket/ISO 606/Simplex ½x⅛/Sprocket ANSI simplex ½x⅛ z30.FCStd` | `unsupported` |
| 40 | `Industrial Design/Jewelry/Diamond/Diamond.FCStd` | `snapshot_fail` |
| 41 | ` Parts/Profiles EN/EN10059 Square steel bars/Square Bar 160 EN10059 S235JR.FCStd` | `pass` |
| 42 | `Architectural Parts/Doors/Wood/Single door with window and trims.FCStd` | `snapshot_fail` |
| 43 | `Pipes and tubes/DN15_Stamped_Flange.FCStd` | `unsupported` |
| 44 | `Sports/Archery/4mm Pole Nock and 3mm Pin Nock.FCStd` | `unsupported` |
| 45 | `ts/Chains/Sprocket/ISO 606/Simplex 6x2,8/Sprocket ISO606 simplex 6x2,8 z16.FCStd` | `pass` |
| 46 | ` Parts/Chains/Sprocket/ISO 606/Simplex 2x1¼/Sprocket ANSI simplex 2x1¼ z40.FCStd` | `unsupported` |
| 47 | `Architectural Parts/Construction blocks/Half concrete block.FCStd` | `snapshot_fail` |
| 48 | `etric/ISO4762 Hexagon socket head cap screws/Screw M24x340 ISO4762 8,8 A2K.FCStd` | `pass` |
| 49 | `ts/Chains/Sprocket/ISO 606/Simplex ½x⁵⁄₁₆/Sprocket ANSI simplex ½x⁵⁄₁₆ z17.FCStd` | `unsupported` |
| 50 | `ts/Chains/Sprocket/ISO 606/Simplex 8x3,0/Sprocket ISO606 simplex 8x3,0 z34.FCStd` | `translator_timeout` |
| 51 | `rts/Fasteners/Bolt Clearance Hole Cylinders/Metric/Normal/M56/M56NormalMin.FCStd` | `pass` |
| 52 | `Profiles EN/EN10056 Unequal Angle Bars/Angle Bar 160x100x12 EN10056 S235JR.FCStd` | `pass` |
| 53 | `Parts/Fasteners/Bolt Clearance Hole Cylinders/Metric/Close/M20/M20CloseMax.FCStd` | `pass` |
| 54 | `ts/Chains/Sprocket/ISO 606/Simplex ¾x⁷⁄₁₆/Sprocket ANSI simplex ¾x⁷⁄₁₆ z16.FCStd` | `unsupported` |
| 55 | `ts/Chains/Sprocket/ISO 606/Simplex ⅜x⁷⁄₃₂/Sprocket ANSI simplex ⅜x⁷⁄₃₂ z19.FCStd` | `unsupported` |
| 56 | `ular Hollow Sections/Rectangular hollow section 500x300x20 EN10219 S235JRH.FCStd` | `pass` |
| 57 | `Metric/ISO4762 Hexagon socket head cap screws/Screw M12x90 ISO4762 8,8 A2K.FCStd` | `pass` |
| 58 | `Electronics Parts/Motors/Stepper/28BYJ-48/28BYJ-48.fcstd` | `snapshot_fail` |
| 59 | `ts/Chains/Sprocket/ISO 606/Simplex 5x2,5/Sprocket ISO606 simplex 5x2,5 z31.FCStd` | `pass` |
| 60 | `/Metric/ISO4762 Hexagon socket head cap screws/Screw M8x80 ISO4762 8,8 A2K.FCStd` | `pass` |
| 61 | `Mechanical Parts/Fasteners/Washers/Metric/DIN463_M33TabWasher.fcstd` | `pass` |
| 62 | `etric/ISO4762 Hexagon socket head cap screws/Screw M16x200 ISO4762 8,8 A2K.FCStd` | `pass` |
| 63 | `/Metric/ISO4762 Hexagon socket head cap screws/Screw M5x14 ISO4762 8,8 A2K.FCStd` | `pass` |
| 64 | `Generic objects/Scale Models/Cement mixer truck/cabin_door.FCStd` | `pass` |
| 65 | `Metric/ISO4762 Hexagon socket head cap screws/Screw M10x16 ISO4762 8,8 A2K.FCStd` | `pass` |
| 66 | `al Parts/Chains/Sprocket/ISO 606/Simplex ½x¼/Sprocket ANSI simplex ½x¼ z28.FCStd` | `unsupported` |
| 67 | `ts/Chains/Sprocket/ISO 606/Simplex ⅜x⁷⁄₃₂/Sprocket ANSI simplex ⅜x⁷⁄₃₂ z13.FCStd` | `unsupported` |
| 68 | `cal Parts/Profiles EN/EN10060 Round steel bars/Round Bar 95 EN10060 S235JR.FCStd` | `pass` |
| 69 | `/Metric/ISO4762 Hexagon socket head cap screws/Screw M4x55 ISO4762 8,8 A2K.FCStd` | `pass` |
| 70 | ` Parts/Chains/Sprocket/ISO 606/Simplex 2x1¼/Sprocket ANSI simplex 2x1¼ z09.FCStd` | `unsupported` |
| 71 | ` Parts/Chains/Sprocket/ISO 606/Simplex 2x1¼/Sprocket ANSI simplex 2x1¼ z10.FCStd` | `unsupported` |
| 72 | `/Profiles EN/EN10056 Equal Angle Bars/Angle Bar L200x200x24 EN10056 S235JR.FCStd` | `pass` |
| 73 | `al Parts/Profiles EN/EN10058 Flat steel bars/Flat Bar 100x6 EN10058 S235JR.FCStd` | `pass` |
| 74 | `Mechanical Parts/Fasteners/Nuts/Metric/T-slot_2020_round_roll-in_nut_M3.FCStd` | `unsupported` |
| 75 | `cal Parts/Profiles EN/EN10060 Round steel bars/Round Bar 14 EN10060 S235JR.FCStd` | `pass` |
| 76 | `219 Square Hollow Sections/Square hollow section 80x80x6.3 EN10219 S235JRH.FCStd` | `pass` |
| 77 | `Mechanical Parts/Mountings/KP08/KP08.FCStd` | `unsupported` |
| 78 | `Electronics Parts/Headers/2.54mm-pitch/male/straight/1x4-male-pin-header.fcstd` | `snapshot_fail` |
| 79 | `arts/Profiles EN/DIN1025-2 HE-B-Profiles/HE-B-Profile 280 DIN1025-2 S235JR.FCStd` | `pass` |
| 80 | `al Parts/Profiles EN/EN10058 Flat steel bars/Flat Bar 60x25 EN10058 S235JR.FCStd` | `pass` |
| 81 | `cal Parts/Profiles EN/EN10058 Flat steel bars/Flat Bar 30x8 EN10058 S235JR.FCStd` | `pass` |
| 82 | `Mechanical Parts/Fasteners/Nuts/Unified/ANSI-ASME-B18.2.2_Hex_Nut_1_4-20.fcstd` | `props_mismatch` |
| 83 | `Architectural Parts/Hydro equipment/WallHungBidet.FCStd` | `pass` |
| 84 | `al Parts/Chains/Sprocket/ISO 606/Simplex ½x¼/Sprocket ANSI simplex ½x¼ z22.FCStd` | `unsupported` |
| 85 | `l Parts/Profiles EN/EN10058 Flat steel bars/Flat Bar 120x20 EN10058 S235JR.FCStd` | `pass` |
| 86 | `ts/Fasteners/Bolt Clearance Hole Cylinders/Metric/Normal/M80/M80NormalMean.FCStd` | `pass` |
| 87 | `219 Square Hollow Sections/Square hollow section 80x80x3.2 EN10219 S235JRH.FCStd` | `pass` |
| 88 | `Electronics Parts/Rotary resistors/rotary-resistor-M64W103KB40.fcstd` | `pass` |
| 89 | `l Parts/Profiles EN/EN10059 Square steel bars/Square Bar 35 EN10059 S235JR.FCStd` | `pass` |
| 90 | `Industrial Design/Tables/GenericTable_1x2m.FCStd` | `pass` |
| 91 | `ular Hollow Sections/Rectangular hollow section 250x150x10 EN10219 S235JRH.FCStd` | `pass` |
| 92 | `Medical Parts/01 Biomedical/OpenLung/Parts/FootPAD.FCStd` | `pass` |
| 93 | `Logistics/Shipping Containers/20_Feet_ISO_Container/side-member_top.FCStd` | `pass` |
| 94 | `Electrical Parts/Batteries/battery_lipo_3_7v_240mah.fcstd` | `snapshot_fail` |
| 95 | `rts/Profiles EN/EN10056 Equal Angle Bars/Angle Bar L90x90x7 EN10056 S235JR.FCStd` | `pass` |
| 96 | `l Parts/Profiles EN/EN10058 Flat steel bars/Flat Bar 140x10 EN10058 S235JR.FCStd` | `pass` |
| 97 | `asteners/Bolt Clearance Hole Cylinders/Unified/Loose/U_5s16/U_5s16LooseMax.FCStd` | `pass` |
| 98 | `/Metric/ISO4762 Hexagon socket head cap screws/Screw M6x30 ISO4762 8,8 A2K.FCStd` | `pass` |
| 99 | `arts/Fasteners/Bolt Clearance Hole Cylinders/Unified/Loose/U_1/U_1LooseMax.FCStd` | `pass` |
| 100 | `etric/ISO4762 Hexagon socket head cap screws/Screw M10x150 ISO4762 8,8 A2K.FCStd` | `pass` |
