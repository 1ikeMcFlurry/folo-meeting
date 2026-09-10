package com.folotoy.meeting;

import org.junit.Test;
import static org.junit.Assert.*;

public class WifiProvisionRulesTest {
    @Test public void validatesUtf8ByteLimits() {
        assertNull(WifiProvisionRules.validate("办公室网络","password1",false));
        assertNotNull(WifiProvisionRules.validate("网".repeat(11),"password1",false));
        assertNotNull(WifiProvisionRules.validate("test","密".repeat(22),false));
    }
    @Test public void openMustBeExplicitAndKeyFormatMustBeValid() {
        assertNotNull(WifiProvisionRules.validate("test","",false));
        assertNull(WifiProvisionRules.validate("test","",true));
        assertNotNull(WifiProvisionRules.validate("test","password",true));
        assertNull(WifiProvisionRules.validate("test","a".repeat(64),false));
        assertNotNull(WifiProvisionRules.validate("test","z".repeat(64),false));
        assertNotNull(WifiProvisionRules.validate("test\nother","password1",false));
    }
}
