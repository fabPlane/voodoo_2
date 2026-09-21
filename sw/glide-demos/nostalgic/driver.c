/* Headless driver: render nostalgic-glide-demo's spinning object through the
 * SpinalVoodoo sim Glide backend (trace-capture libglide2x). Renders a fixed
 * number of frames (default 60) so the Glide command stream is captured
 * deterministically, then exits. No VGA, no keyboard, no DOS timer.
 *
 *   NG_OBJ=torus (default)  -- torus.3d + procedurally generated env-map
 *   NG_OBJ=cube             -- procedural cube (needs the external texture pack)
 *   argv[1]                 -- frame count
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "bumpobj.h"
#include "raster.h"
#include "texman.h"

extern void compute_lookup_tables(void);                 /* math3d.c   */
extern int  initialize_torus(object3d_t *, texturemanager_t *); /* bumpmain.c */
extern int  initialize_cube(object3d_t *, texturemanager_t *);  /* bumpmain.c */

int main(int argc, char **argv)
{
    int frames = (argc > 1) ? atoi(argv[1]) : 60;
    const char *which = getenv("NG_OBJ");
    int use_cube = (which && strcmp(which, "cube") == 0);

    drawcontext_t dc;
    object3d_t obj;
    texturemanager_t tm;
    vector3d_t light = { -1, 0, 0 };
    int i;

    if (frames <= 0) frames = 60;

    compute_lookup_tables();

    if (!create_manager(&tm)) {
        fprintf(stderr, "driver: create_manager failed\n");
        return 1;
    }

    if (use_cube) {
        if (!initialize_cube(&obj, &tm)) {
            fprintf(stderr, "driver: initialize_cube failed (texture pack missing?)\n");
            return 1;
        }
    } else {
        if (!initialize_torus(&obj, &tm)) {
            fprintf(stderr, "driver: initialize_torus failed (assets/models/torus.3d?)\n");
            return 1;
        }
    }

    if (!create_context(&dc)) {
        fprintf(stderr, "driver: create_context failed (no Voodoo backend)\n");
        return 1;
    }

    set_context_palette(&dc, obj.faces[0].mapper.texture->bitmap.palette);
    set_draw_mode(&dc, use_cube ? MODE_TEXTURE : MODE_ENVMAP);

    fprintf(stderr, "driver: rendering %d frames of %s\n",
            frames, use_cube ? "cube" : "torus");

    for (i = 0; i < frames; i++) {
        update_object3d(&obj);
        draw_object3d(&obj, &light, &dc);
        flip_buffer(&dc);
    }

    destroy_context(&dc);
    unload_object3d(&obj);
    destroy_manager(&tm);
    return 0;
}
