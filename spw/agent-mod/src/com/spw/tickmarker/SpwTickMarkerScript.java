package com.spw.tickmarker;

import com.fs.starfarer.api.EveryFrameScript;

/**
 * Fires one {@link TickBoundaryEvent} per campaign-engine tick.
 *
 * Registered as a transient script (not saved into the player's save
 * file) by {@link SpwTickMarkerModPlugin}. It never touches game state:
 * it does not read or write anything on the sector, fleet, or player --
 * its only side effect is committing a JFR event.
 */
public class SpwTickMarkerScript implements EveryFrameScript {

    private long tickIndex = 0;

    @Override
    public boolean isDone() {
        // Never finishes on its own; removed only if the mod is disabled
        // or the game exits.
        return false;
    }

    @Override
    public boolean runWhilePaused() {
        // Keep marking ticks while the campaign is paused (e.g. a menu is
        // open) so a gap in tick events is never confused with the engine
        // having stalled.
        return true;
    }

    @Override
    public void advance(float amount) {
        TickBoundaryEvent event = new TickBoundaryEvent();
        if (event.shouldCommit()) {
            event.tickIndex = tickIndex;
            event.elapsedSeconds = amount;
            event.commit();
        }
        tickIndex++;
    }
}
