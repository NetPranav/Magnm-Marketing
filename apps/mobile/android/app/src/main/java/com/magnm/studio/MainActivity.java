package com.magnm.studio;

import android.content.Intent;
import android.os.Bundle;
import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {

    @Override
    public void onCreate(Bundle savedInstanceState) {
        registerPlugin(DisabledCorePlugins.Http.class);
        registerPlugin(DisabledCorePlugins.WebViewControl.class);
        registerPlugin(MagnmNativePlugin.class);

        if (savedInstanceState != null) {
            Intent current = getIntent();
            if (current != null && Intent.ACTION_SEND.equals(current.getAction())) {
                setIntent(new Intent(Intent.ACTION_MAIN));
            }
        }

        super.onCreate(savedInstanceState);
    }
}
