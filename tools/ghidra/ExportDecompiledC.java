// Export every non-external function in the current program as Ghidra decompiler C.
// @category Export

import java.io.File;
import java.io.PrintWriter;

import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionIterator;

public class ExportDecompiledC extends GhidraScript {
    @Override
    protected void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length != 1) {
            throw new IllegalArgumentException("usage: ExportDecompiledC.java OUTPUT_DIR");
        }

        File outputDir = new File(args[0]);
        if (!outputDir.isDirectory() && !outputDir.mkdirs()) {
            throw new IllegalStateException("cannot create output directory: " + outputDir);
        }

        String safeName = currentProgram.getName().replaceAll("[^A-Za-z0-9._-]", "_");
        File outputFile = new File(outputDir, safeName + ".decompiled.c");
        File indexFile = new File(outputDir, safeName + ".functions.tsv");

        DecompInterface decompiler = new DecompInterface();
        decompiler.toggleCCode(true);
        decompiler.toggleSyntaxTree(true);
        if (!decompiler.openProgram(currentProgram)) {
            throw new IllegalStateException("decompiler could not open " + currentProgram.getName());
        }

        int exported = 0;
        int failed = 0;
        try (PrintWriter out = new PrintWriter(outputFile, "UTF-8");
             PrintWriter index = new PrintWriter(indexFile, "UTF-8")) {
            out.println("/* Ghidra decompiler export for " + currentProgram.getExecutablePath() + " */");
            out.println("/* Language: " + currentProgram.getLanguageID() + " */");
            out.println();
            index.println("entry\tname\tsignature\tstatus");

            FunctionIterator functions = currentProgram.getFunctionManager().getFunctions(true);
            while (functions.hasNext() && !monitor.isCancelled()) {
                Function function = functions.next();
                if (function.isExternal()) {
                    continue;
                }
                monitor.setMessage("Decompiling " + function.getName());
                DecompileResults result = decompiler.decompileFunction(function, 120, monitor);
                String status;
                if (result.decompileCompleted() && result.getDecompiledFunction() != null) {
                    out.println("/* " + function.getEntryPoint() + " | " + function.getSignature() + " */");
                    out.println(result.getDecompiledFunction().getC());
                    out.println();
                    status = "ok";
                    exported++;
                } else {
                    String message = result.getErrorMessage();
                    status = "failed: " + (message == null ? "unknown error" : message.replace('\t', ' '));
                    failed++;
                }
                index.printf("%s\t%s\t%s\t%s%n",
                    function.getEntryPoint(), function.getName(),
                    function.getSignature().toString().replace('\t', ' '), status);
            }
        } finally {
            decompiler.dispose();
        }

        println("Exported " + exported + " functions; failures " + failed + "; output " + outputFile);
    }
}
