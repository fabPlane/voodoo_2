// Retry one function by entry address with a caller-selected timeout.
// @category Export

import java.io.File;
import java.io.PrintWriter;

import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileOptions;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;

public class ExportFunctionByAddress extends GhidraScript {
    @Override
    protected void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length != 4) {
            throw new IllegalArgumentException(
                "usage: ExportFunctionByAddress.java OUTPUT_DIR ADDRESS TIMEOUT_SECONDS MAX_PAYLOAD_MB");
        }

        File outputDir = new File(args[0]);
        if (!outputDir.isDirectory() && !outputDir.mkdirs()) {
            throw new IllegalStateException("cannot create output directory: " + outputDir);
        }
        Address address = toAddr(args[1]);
        int timeout = Integer.parseInt(args[2]);
        int maxPayloadMBytes = Integer.parseInt(args[3]);
        Function function = currentProgram.getFunctionManager().getFunctionAt(address);
        if (function == null) {
            throw new IllegalArgumentException("no function at " + address);
        }

        DecompInterface decompiler = new DecompInterface();
        decompiler.toggleCCode(true);
        decompiler.toggleSyntaxTree(true);
        DecompileOptions options = new DecompileOptions();
        options.grabFromProgram(currentProgram);
        options.setMaxPayloadMBytes(maxPayloadMBytes);
        decompiler.setOptions(options);
        if (!decompiler.openProgram(currentProgram)) {
            throw new IllegalStateException("decompiler could not open " + currentProgram.getName());
        }

        String safeProgram = currentProgram.getName().replaceAll("[^A-Za-z0-9._-]", "_");
        File output = new File(outputDir, safeProgram + ".retry." + args[1] + ".c");
        try {
            DecompileResults result = decompiler.decompileFunction(function, timeout, monitor);
            if (!result.decompileCompleted() || result.getDecompiledFunction() == null) {
                throw new IllegalStateException("retry failed: " + result.getErrorMessage());
            }
            try (PrintWriter out = new PrintWriter(output, "UTF-8")) {
                out.println("/* Targeted Ghidra retry for " + currentProgram.getExecutablePath() + " */");
                out.println("/* " + function.getEntryPoint() + " | " + function.getSignature() + " */");
                out.println(result.getDecompiledFunction().getC());
            }
            println("Retry exported " + function.getName() + " at " + address + " to " + output);
        } finally {
            decompiler.dispose();
        }
    }
}
