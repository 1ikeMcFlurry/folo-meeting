package com.folotoy.meeting;

import android.bluetooth.*;
import android.bluetooth.le.*;
import android.content.*;
import android.os.*;
import org.json.JSONObject;
import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.concurrent.*;
import java.util.function.Consumer;
import java.io.IOException;

/** Foreground, one-phone GATT client; recording continues after this client closes. */
@android.annotation.SuppressLint("MissingPermission") // Entry points check permissions; callbacks handle revocation.
final class BleClient {
    static final UUID SERVICE=UUID.fromString("f0100000-464f-4c4f-4d45-455400000001");
    static final UUID RX=UUID.fromString("f0100000-464f-4c4f-4d45-455400000002");
    static final UUID TX=UUID.fromString("f0100000-464f-4c4f-4d45-455400000003");
    private final Context context;
    private final Handler main=new Handler(Looper.getMainLooper());
    private volatile BluetoothGatt gatt;
    private volatile CountDownLatch latch;
    private volatile int status;
    private volatile byte[] value;
    private volatile int mtu=23;
    private int sequence;
    private ScanCallback scanCallback;
    private Runnable disconnected;
    BleClient(Context context) { this.context=context; }
    void onDisconnected(Runnable action) { disconnected=action; }
    private boolean canConnect() { return Build.VERSION.SDK_INT<31 || context.checkSelfPermission(android.Manifest.permission.BLUETOOTH_CONNECT)==android.content.pm.PackageManager.PERMISSION_GRANTED; }
    private void requirePermissions(boolean scan) throws IOException {
        if(!canConnect() || (scan && Build.VERSION.SDK_INT>=31 && context.checkSelfPermission(android.Manifest.permission.BLUETOOTH_SCAN)!=android.content.pm.PackageManager.PERMISSION_GRANTED))
            throw new IOException("请允许附近设备权限");
        if(scan && Build.VERSION.SDK_INT<31 && context.checkSelfPermission(android.Manifest.permission.ACCESS_FINE_LOCATION)!=android.content.pm.PackageManager.PERMISSION_GRANTED)
            throw new IOException("请允许搜索蓝牙设备所需的位置权限");
    }
    private BluetoothAdapter adapter() { return context.getSystemService(BluetoothManager.class).getAdapter(); }
    boolean connected() { return gatt!=null && gatt.getService(SERVICE)!=null; }
    BluetoothDevice connectedDevice() { BluetoothGatt current=gatt; return current==null?null:current.getDevice(); }
    private final BluetoothGattCallback callback=new BluetoothGattCallback() {
        private void finish(BluetoothGatt g,int code) { if(g!=gatt) return; status=code; CountDownLatch l=latch; if(l!=null) l.countDown(); }
        @Override public void onConnectionStateChange(BluetoothGatt g,int code,int state) {
            if(state==BluetoothProfile.STATE_CONNECTED) finish(g,code);
            else if(state==BluetoothProfile.STATE_DISCONNECTED) {
                boolean wasCurrent=g==gatt; finish(g,code==0?257:code); if(wasCurrent) gatt=null;
                try { g.close(); } catch(SecurityException ignored) {}
                if(wasCurrent && disconnected!=null) main.post(disconnected);
            }
        }
        @Override public void onServicesDiscovered(BluetoothGatt g,int code) { finish(g,code); }
        @Override public void onMtuChanged(BluetoothGatt g,int size,int code) { if(code==0) mtu=size; finish(g,code); }
        @Override public void onCharacteristicWrite(BluetoothGatt g,BluetoothGattCharacteristic c,int code) { finish(g,code); }
        @Override public void onCharacteristicRead(BluetoothGatt g,BluetoothGattCharacteristic c,int code) { value=c.getValue(); finish(g,code); }
        @Override public void onCharacteristicRead(BluetoothGatt g,BluetoothGattCharacteristic c,byte[] bytes,int code) { value=bytes; finish(g,code); }
    };
    void scan(Consumer<BluetoothDevice> found,Consumer<String> done) throws Exception {
        requirePermissions(true);
        BluetoothAdapter adapter=adapter(); if(adapter==null || !adapter.isEnabled()) throw new IOException("请先打开手机蓝牙");
        stopScan();
        scanCallback=new ScanCallback() {
            @Override public void onScanResult(int type,ScanResult result) { main.post(()->found.accept(result.getDevice())); }
            @Override public void onScanFailed(int code) { main.post(()->done.accept("蓝牙搜索失败（"+code+"），请检查权限")); }
        };
        adapter.getBluetoothLeScanner().startScan(List.of(new ScanFilter.Builder().setServiceUuid(new ParcelUuid(SERVICE)).build()),
            new ScanSettings.Builder().setScanMode(ScanSettings.SCAN_MODE_LOW_LATENCY).build(),scanCallback);
        main.postDelayed(()-> { stopScan(); done.accept("搜索完成；未发现设备时，长按设备上键再搜索"); },10000);
    }
    void stopScan() {
        if(scanCallback!=null) { try { BluetoothAdapter a=adapter(); if(a!=null && a.isEnabled()) a.getBluetoothLeScanner().stopScan(scanCallback); } catch(SecurityException ignored) {} scanCallback=null; }
    }
    private void begin() { status=-1; value=null; latch=new CountDownLatch(1); }
    private void await(int seconds) throws Exception {
        if(!latch.await(seconds,TimeUnit.SECONDS)) throw new IOException("设备响应超时，请靠近设备后重新连接");
        if(status!=0) throw new IOException("蓝牙通信失败（"+status+"），请重新连接；首次连接需完成配对");
    }
    synchronized void connect(BluetoothDevice device) throws Exception {
        requirePermissions(false);
        stopScan(); close(); begin();
        gatt=device.connectGatt(context,false,callback,BluetoothDevice.TRANSPORT_LE);
        try {
            await(20);
            if(device.getBondState()!=BluetoothDevice.BOND_BONDED) {
                CountDownLatch bonded=new CountDownLatch(1);
                BroadcastReceiver receiver=new BroadcastReceiver() {
                    @Override public void onReceive(Context c,Intent intent) {
                        BluetoothDevice d=intent.getParcelableExtra(BluetoothDevice.EXTRA_DEVICE);
                        if(d!=null && d.getAddress().equals(device.getAddress()) && d.getBondState()==BluetoothDevice.BOND_BONDED) bonded.countDown();
                    }
                };
                if(Build.VERSION.SDK_INT>=33) context.registerReceiver(receiver,new IntentFilter(BluetoothDevice.ACTION_BOND_STATE_CHANGED),Context.RECEIVER_EXPORTED);
                else context.registerReceiver(receiver,new IntentFilter(BluetoothDevice.ACTION_BOND_STATE_CHANGED));
                try {
                    if(!device.createBond() && device.getBondState()!=BluetoothDevice.BOND_BONDING) throw new IOException("无法配对，请长按设备上键重试");
                    if(!bonded.await(75,TimeUnit.SECONDS)) throw new IOException("配对未完成，请输入设备屏幕上的六位配对码");
                } finally { context.unregisterReceiver(receiver); }
            }
            begin(); if(!gatt.discoverServices()) throw new IOException("无法读取设备服务"); await(15);
            if(gatt.getService(SERVICE)==null) throw new IOException("设备固件不支持会议配置，请更新固件");
            mtu=23; begin(); if(gatt.requestMtu(185)) await(10);
        } catch(Exception e) { close(); throw e; }
    }
    synchronized JSONObject command(JSONObject command) throws Exception {
        requirePermissions(false);
        BluetoothGatt current=gatt; if(current==null) throw new IOException("请先连接录音设备");
        int seq=++sequence; command.put("seq",seq);
        byte[] bytes=command.toString().getBytes(StandardCharsets.UTF_8);
        if(bytes.length>=4096) throw new IOException("配置内容过长");
        BluetoothGattService service=current.getService(SERVICE);
        if(service==null) throw new IOException("设备服务不可用，请重新连接");
        BluetoothGattCharacteristic rx=service.getCharacteristic(RX), tx=service.getCharacteristic(TX);
        try {
            int size=Math.max(1,Math.min(181,mtu-3)-4);
            for(int offset=0;offset<bytes.length;offset+=size) {
                int count=Math.min(size,bytes.length-offset); byte[] frame=new byte[count+4];
                frame[0]=(byte)bytes.length; frame[1]=(byte)(bytes.length>>8); frame[2]=(byte)offset; frame[3]=(byte)(offset>>8);
                System.arraycopy(bytes,offset,frame,4,count); begin();
                boolean accepted;
                if(Build.VERSION.SDK_INT>=33) accepted=current.writeCharacteristic(rx,frame,BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT)==BluetoothStatusCodes.SUCCESS;
                else { rx.setWriteType(BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT); rx.setValue(frame); accepted=current.writeCharacteristic(rx); }
                if(!accepted) throw new IOException("设备正忙，请稍后重试");
                await(15); Arrays.fill(frame,(byte)0);
            }
            long deadline=SystemClock.elapsedRealtime()+45000;
            do {
                begin(); if(!current.readCharacteristic(tx)) throw new IOException("无法读取设备回执"); await(15);
                JSONObject response=new JSONObject(new String(value,StandardCharsets.UTF_8));
                if(response.optInt("seq",-1)==seq) {
                    if(response.has("ok") && !response.optBoolean("ok")) throw new IOException(error(response.optString("error")));
                    return response;
                }
                Thread.sleep(250);
            } while(SystemClock.elapsedRealtime()<deadline);
            throw new IOException("设备尚未确认操作，请重新查询状态，避免重复创建会议或文档");
        } finally { Arrays.fill(bytes,(byte)0); }
    }
    private static String error(String code) {
        return switch(code) {
            case "wifi_failed" -> "Wi-Fi 连接失败，请检查 2.4 GHz 网络、密码和信号";
            case "invalid_settings_or_storage" -> "配置无效或保存失败；自动归档需要先授权设备访问飞书";
            case "busy_or_invalid_command" -> "设备还有未完成的任务，或当前不能执行此操作，请查询会议状态";
            default -> "设备未确认操作："+code;
        };
    }
    void close() { BluetoothGatt old=gatt; gatt=null; if(old!=null) { try { old.disconnect(); old.close(); } catch(SecurityException ignored) {} } }
}
