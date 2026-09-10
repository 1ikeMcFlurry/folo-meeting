package com.folotoy.meeting;

import android.app.Activity;
import android.os.Build;
import android.os.Bundle;
import android.view.inputmethod.InputMethodManager;
import android.view.autofill.AutofillManager;
import android.window.OnBackInvokedCallback;
import android.window.OnBackInvokedDispatcher;

/** Both the system gesture and visible parent links use the same navigation path. */
abstract class BrandActivity extends Activity {
    private OnBackInvokedCallback backCallback;
    @Override protected void onCreate(Bundle saved) {
        super.onCreate(saved);
        if(Build.VERSION.SDK_INT>=33) {
            backCallback=this::navigateBack;
            getOnBackInvokedDispatcher().registerOnBackInvokedCallback(OnBackInvokedDispatcher.PRIORITY_DEFAULT,backCallback);
        }
    }
    protected abstract void navigateBack();
    private void cancelAutofill() {
        // These are API credentials and local drafts, not account sign-in forms.
        // End this Activity's context without prompting to save a discarded edit.
        AutofillManager manager=getSystemService(AutofillManager.class);
        if(manager!=null) manager.cancel();
    }
    protected void hideKeyboard() {
        cancelAutofill();
        ((InputMethodManager)getSystemService(INPUT_METHOD_SERVICE)).hideSoftInputFromWindow(getWindow().getDecorView().getWindowToken(),0);
    }
    @Override protected void onPause() { cancelAutofill(); super.onPause(); }
    @android.annotation.SuppressLint("GestureBackNavigation") // API 26–32 fallback; API 33+ uses the dispatcher above.
    @Override public void onBackPressed() { navigateBack(); }
    @Override protected void onDestroy() {
        if(Build.VERSION.SDK_INT>=33 && backCallback!=null) getOnBackInvokedDispatcher().unregisterOnBackInvokedCallback(backCallback);
        super.onDestroy();
    }
}
