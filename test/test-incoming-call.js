#!/usr/bin/env node

// Test script to simulate an incoming call via Event Grid webhook
import fetch from 'node-fetch';

const serverUrl = 'http://localhost:8080';

// Simulate an IncomingCall event from Azure Event Grid
const incomingCallEvent = {
  id: "test-event-123",
  eventType: "Microsoft.Communication.IncomingCall",
  data: {
    incomingCallContext: "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJjYWxsZXJJZCI6eyJyYXdJZCI6IjQ6KzE0MDg1NTUwMTIzIn0sInRhcmdldElkIjp7InJhd0lkIjoiNDorMTQwODU1NTAxMjMifSwidGVuYW50SWQiOiJ0ZXN0LXRlbmFudCIsInNlcnZlckNhbGxJZCI6InRlc3Qtc2VydmVyLWNhbGwtaWQifQ.test-signature",
    from: {
      phoneNumber: { value: "+14085550123" },
      rawId: "4:+14085550123"
    },
    to: {
      phoneNumber: { value: "+14085550456" },
      rawId: "4:+14085550456"
    },
    serverCallId: "test-server-call-id-123",
    correlationId: "test-correlation-id"
  },
  eventTime: new Date().toISOString(),
  subject: "phonenumber/+14085550456",
  dataVersion: "1.0"
};

async function testIncomingCall() {
  try {
    console.log('🔥 Simulating incoming call...');
    console.log('Event payload:', JSON.stringify(incomingCallEvent, null, 2));
    
    const response = await fetch(`${serverUrl}/api/events`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'aeg-event-type': 'Notification'
      },
      body: JSON.stringify([incomingCallEvent])
    });
    
    console.log('📡 Response status:', response.status);
    const responseText = await response.text();
    console.log('📨 Response body:', responseText);
    
    if (response.ok) {
      console.log('✅ Incoming call event processed successfully!');
    } else {
      console.log('❌ Error processing incoming call event');
    }
    
  } catch (error) {
    console.error('🚨 Error testing incoming call:', error);
  }
}

async function testCallDisconnected() {
  // Simulate a CallDisconnected event
  const disconnectEvent = {
    id: "test-disconnect-event-123",
    eventType: "Microsoft.Communication.CallDisconnected",
    data: {
      serverCallId: "test-server-call-id-123",
      callConnectionId: "test-connection-id",
      correlationId: "test-correlation-id"
    },
    eventTime: new Date().toISOString(),
    subject: "phonenumber/+14085550456",
    dataVersion: "1.0"
  };
  
  try {
    console.log('\n🔌 Simulating call disconnect...');
    
    const response = await fetch(`${serverUrl}/api/events`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'aeg-event-type': 'Notification'
      },
      body: JSON.stringify([disconnectEvent])
    });
    
    console.log('📡 Disconnect response status:', response.status);
    const responseText = await response.text();
    console.log('📨 Disconnect response body:', responseText);
    
    if (response.ok) {
      console.log('✅ Call disconnect event processed successfully!');
    } else {
      console.log('❌ Error processing call disconnect event');
    }
    
  } catch (error) {
    console.error('🚨 Error testing call disconnect:', error);
  }
}

// Run the test
console.log('🧪 Testing incoming call and disconnect flow...\n');
await testIncomingCall();

// Wait a moment, then test disconnect
setTimeout(async () => {
  await testCallDisconnected();
}, 2000);
