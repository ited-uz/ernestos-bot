package uz.ernestos.app;

import android.webkit.WebSettings;
import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {

    /**
     * The phone's own text size (Settings → Display → Font size) reaches the
     * app, but only up to this much: Android allows 130–200%, and beyond
     * ~115% the four-tab rows and the bottom bar no longer fit a 360dp
     * screen, so words were cut in half. Smaller-than-normal is honoured too.
     */
    private static final float MAX_FONT_SCALE = 1.15f;
    private static final float MIN_FONT_SCALE = 0.85f;

    @Override
    public void onResume() {
        super.onResume();
        applyFontScale();
    }

    private void applyFontScale() {
        if (bridge == null || bridge.getWebView() == null) return;
        float scale = getResources().getConfiguration().fontScale;
        int zoom = Math.round(Math.max(MIN_FONT_SCALE, Math.min(scale, MAX_FONT_SCALE)) * 100);
        WebSettings settings = bridge.getWebView().getSettings();
        if (settings.getTextZoom() != zoom) settings.setTextZoom(zoom);
    }
}
