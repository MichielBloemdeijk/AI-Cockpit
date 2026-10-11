package nl.sander.aicockpit.screenfixture;

import android.app.Activity;
import android.app.Instrumentation;
import android.graphics.Rect;
import android.os.Bundle;
import android.util.Xml;
import android.view.accessibility.AccessibilityNodeInfo;
import android.view.accessibility.AccessibilityWindowInfo;
import android.accessibilityservice.AccessibilityServiceInfo;
import java.io.StringWriter;
import org.xmlpull.v1.XmlSerializer;

/** Test APK only. Read active accessibility nodes without a global-idle wait.
 * Runs in the separate marker package, never restarting the app under test.
 */
public final class HierarchyDumpInstrumentation extends Instrumentation {
    private int remaining = 1000;

    @Override public void onCreate(Bundle arguments) {
        super.onCreate(arguments);
        start();
    }

    @Override public void onStart() {
        Bundle result = new Bundle();
        AccessibilityNodeInfo root = null;
        try {
            AccessibilityServiceInfo service = getUiAutomation().getServiceInfo();
            service.flags |= AccessibilityServiceInfo.FLAG_RETRIEVE_INTERACTIVE_WINDOWS;
            getUiAutomation().setServiceInfo(service);
            StringWriter output = new StringWriter();
            XmlSerializer xml = Xml.newSerializer();
            xml.setOutput(output);
            xml.startDocument("UTF-8", true);
            xml.startTag("", "hierarchy");
            // Notification shade may have no focused application window. Read
            // current interactive windows, including SystemUI, without an idle wait.
            boolean observed = false;
            for (int attempt = 0; attempt < 10 && !observed; attempt++) {
                for (AccessibilityWindowInfo window : getUiAutomation().getWindows()) {
                    AccessibilityNodeInfo windowRoot = window.getRoot();
                    if (windowRoot != null) {
                        try { write(xml, windowRoot, 0); observed = true; }
                        finally { windowRoot.recycle(); }
                    }
                }
                if (!observed) android.os.SystemClock.sleep(100);
            }
            if (!observed) {
                root = getUiAutomation().getRootInActiveWindow();
                if (root == null) throw new IllegalStateException("No active accessibility window");
                write(xml, root, 0);
            }
            xml.endTag("", "hierarchy");
            xml.endDocument();
            result.putString("hierarchy", output.toString());
            finish(Activity.RESULT_OK, result);
        } catch (Exception error) {
            result.putString("error", error.getClass().getSimpleName() + ": " + error.getMessage());
            finish(Activity.RESULT_CANCELED, result);
        } finally {
            if (root != null) root.recycle();
        }
    }

    private static String text(CharSequence value) {
        if (value == null) return "";
        String valueText = value.toString();
        return valueText.substring(0, Math.min(512, valueText.length()));
    }

    private void write(XmlSerializer xml, AccessibilityNodeInfo node, int depth) throws Exception {
        if (depth > 60 || remaining-- <= 0) return;
        Rect bounds = new Rect();
        node.getBoundsInScreen(bounds);
        xml.startTag("", "node");
        xml.attribute("", "class", text(node.getClassName()));
        xml.attribute("", "package", text(node.getPackageName()));
        xml.attribute("", "resource-id", text(node.getViewIdResourceName()));
        xml.attribute("", "text", node.isPassword() ? "" : text(node.getText()));
        xml.attribute("", "content-desc", node.isPassword() ? "" : text(node.getContentDescription()));
        xml.attribute("", "clickable", Boolean.toString(node.isClickable()));
        xml.attribute("", "enabled", Boolean.toString(node.isEnabled()));
        xml.attribute("", "bounds", "[" + bounds.left + "," + bounds.top + "][" + bounds.right + "," + bounds.bottom + "]");
        for (int i = 0; i < node.getChildCount(); i++) {
            AccessibilityNodeInfo child = node.getChild(i);
            if (child == null) continue;
            try { write(xml, child, depth + 1); }
            finally { child.recycle(); }
        }
        xml.endTag("", "node");
    }
}
