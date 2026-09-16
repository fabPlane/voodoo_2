// Preliminary full-height PCI bracket for KiCad fit visualization only.
// Connector openings follow the current Rev A courtyard stack, not released
// manufacturer panel-cutout drawings. Units are millimetres.

plate_thickness = 0.8;
plate_length = 103.5;
plate_height = 21.5;

module opening(y0, width, z0, height) {
    translate([-1.5, y0, z0]) cube([3.0, width, height]);
}

// KiCad's STEP importer reverses the model Y axis relative to OpenSCAD's STL
// convention. Mirror here so the bracket openings follow increasing board Y.
mirror([0, 1, 0]) union() {
    difference() {
        translate([-0.8, -2.0, -3.0])
            cube([plate_thickness, plate_length, plate_height]);

        // J2 HD15: generous preview opening around the connector shell.
        opening(3.0, 29.9, 1.5, 14.0);

        // J4 HDMI-A.
        opening(37.5, 15.4, 2.0, 7.4);

        // J5 USB-A.
        opening(57.0, 13.3, 1.8, 8.0);

        // J6 RJ45.
        opening(73.9, 16.8, 1.2, 15.8);
    }

    // Simplified 90-degree chassis retention tab at the top of the bracket.
    translate([-9.0, -2.8, -3.0]) cube([9.0, 0.8, plate_height]);
}
