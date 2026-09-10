# Preserve reflection, EventBus subscribers and protocol models in this trial.
-keep,allowoptimization class ** { *; }
-dontobfuscate
-assumenosideeffects class android.util.Log {
    public static int v(...);
    public static int d(...);
    public static int i(...);
    public static int w(...);
    public static int e(...);
    public static int wtf(...);
}
