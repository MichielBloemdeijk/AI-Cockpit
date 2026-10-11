package nl.sander.aicockpit.screenfixture;

import android.app.Activity;
import android.graphics.Color;
import android.graphics.Canvas;
import android.util.Log;
import android.os.Bundle;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.view.WindowManager;
import android.view.Gravity;
import android.widget.LinearLayout;
import android.widget.TextView;

/** A separate, non-secure foreground app for Android assistant screenshot tests. */
public final class MarkerActivity extends Activity {
    private TextView marker;
    private TextView color;
    private boolean screenB;
    private boolean screenBDrawn;
    private final BroadcastReceiver changes = new BroadcastReceiver() {
        @Override public void onReceive(Context context, Intent intent) {
            screenB = intent.getBooleanExtra("screen_b", false);
            screenBDrawn = false;
            marker.setText(screenB ? "FIXTURE-B-VISIBLE-MARKER" : "FIXTURE-A-VISIBLE-MARKER");
            String subject = intent.getStringExtra("subject");
            if (subject != null) marker.append("\n" + subject);
            color.setText(screenB ? "RED PANEL" : "BLUE PANEL");
            color.setBackgroundColor(screenB ? Color.rgb(220, 30, 0) : Color.rgb(0, 40, 220));
            if (intent.getBooleanExtra("secure", false)) getWindow().addFlags(WindowManager.LayoutParams.FLAG_SECURE);
            else getWindow().clearFlags(WindowManager.LayoutParams.FLAG_SECURE);
        }
    };
    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(Color.WHITE);

        marker = new TextView(this);
        String subject = getIntent().getStringExtra("subject");
        marker.setText("FIXTURE-A-VISIBLE-MARKER");
        if (subject != null) marker.append("\n" + subject);
        marker.setTextColor(Color.BLACK);
        marker.setTextSize(28);
        marker.setGravity(Gravity.CENTER);
        root.addView(marker, new LinearLayout.LayoutParams(-1, 0, 1));

        color = new TextView(this) {
            @Override protected void onDraw(Canvas canvas) {
                super.onDraw(canvas);
                if (screenB && !screenBDrawn) {
                    screenBDrawn = true;
                    Log.i("JarvisScreenFixture", "screen_b_drawn=true");
                }
            }
        };
        color.setText("BLUE PANEL");
        color.setTextColor(Color.WHITE);
        color.setTextSize(24);
        color.setGravity(Gravity.CENTER);
        color.setBackgroundColor(Color.rgb(0, 40, 220));
        root.addView(color, new LinearLayout.LayoutParams(-1, 0, 1));
        setContentView(root);
        registerReceiver(changes, new IntentFilter("nl.sander.aicockpit.screenfixture.CHANGE"), Context.RECEIVER_EXPORTED);
    }
    @Override protected void onDestroy() { unregisterReceiver(changes); super.onDestroy(); }
}
