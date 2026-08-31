package com.spw.tickmarker;

import jdk.jfr.Category;
import jdk.jfr.Description;
import jdk.jfr.Event;
import jdk.jfr.Label;
import jdk.jfr.Name;

/**
 * One campaign-engine tick boundary, emitted by the SPW Tick Marker mod.
 *
 * This is the DEEP_DIAGNOSTIC-only custom event described in the SPW
 * design doc's "Capture levels" section. It carries no gameplay state --
 * only the elapsed-time argument the engine itself passes to every
 * EveryFrameScript's advance() call, plus a running tick counter -- so the
 * external SPW analyzer can bucket other JFR samples (CPU, allocation) by
 * which tick they fell in.
 */
@Name("com.spw.TickBoundary")
@Label("SPW Tick Boundary")
@Category({"Starsector Performance Workbench"})
@Description("Marks one Starsector campaign-engine tick boundary; emitted by the opt-in SPW Tick Marker mod, never by SPW itself or by the game's own code.")
public class TickBoundaryEvent extends Event {

    @Label("Tick Index")
    @Description("Monotonically increasing count of ticks observed since the SPW Tick Marker script was registered.")
    public long tickIndex;

    @Label("Elapsed Seconds")
    @Description("The elapsed-time argument the engine passed to this tick's advance() call.")
    public float elapsedSeconds;
}
