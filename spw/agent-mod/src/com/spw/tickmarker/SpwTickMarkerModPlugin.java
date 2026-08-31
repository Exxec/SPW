package com.spw.tickmarker;

import com.fs.starfarer.api.BaseModPlugin;
import com.fs.starfarer.api.Global;

/**
 * Entry point for the SPW Tick Marker mod.
 *
 * This is an ordinary Starsector mod using only the public, documented
 * modding API (EveryFrameScript / ModPlugin) -- the same mechanism any
 * other mod uses to run logic once per campaign tick. It is not a Java
 * agent and does not instrument, patch, or otherwise touch Starsector's
 * own (obfuscated) core classes; it only registers a script the game
 * itself calls, exactly as documented for mod authors.
 */
public class SpwTickMarkerModPlugin extends BaseModPlugin {

    @Override
    public void onGameLoad(boolean newGame) {
        Global.getSector().addTransientScript(new SpwTickMarkerScript());
    }
}
