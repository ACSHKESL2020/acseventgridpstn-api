// Debug script to test recording during a real session
import dotenv from 'dotenv';
import path from 'path';
dotenv.config({ path: path.resolve(process.cwd(), '.env') });

import { startRecording, writePcm, stopRecording, cleanupSessionTemp } from './src/services/recorderService.js';
import fs from 'fs';

console.log('🔧 Recording Debug Tool');
console.log('This will help diagnose recording issues during voice sessions');

// Test if environment variables are set correctly
console.log('\n📋 Environment Check:');
console.log('MIN_RECORDING_BYTES:', process.env.MIN_RECORDING_BYTES || '1024 (default)');
console.log('SESSION_TEMP_DIR:', process.env.SESSION_TEMP_DIR || '/tmp/voice-sessions (default)');

// Test basic recording functionality
console.log('\n🧪 Basic Recording Test:');
const testSessionId = 'debug-' + Date.now();

try {
  const { sessionId, outPath } = startRecording(testSessionId);
  console.log('✅ Recording started for session:', sessionId);
  console.log('📁 Output path:', outPath);
  
  // Simulate minimal audio (like what might happen in a real session)
  console.log('\n📊 Testing with minimal audio data...');
  const minimalPcm = Buffer.alloc(100, 0); // Very small amount
  writePcm(testSessionId, minimalPcm);
  
  await new Promise(resolve => setTimeout(resolve, 500));
  
  const result1 = await stopRecording(testSessionId);
  console.log('Result with minimal data:', result1 ? `✅ File created (${fs.statSync(result1.outPath).size} bytes)` : '❌ File too small, skipped');
  
  if (result1) cleanupSessionTemp(testSessionId);
  
  // Test with more realistic audio data
  console.log('\n📊 Testing with realistic audio data...');
  const testSessionId2 = 'debug2-' + Date.now();
  const { sessionId: sid2, outPath: path2 } = startRecording(testSessionId2);
  
  // Simulate 3 seconds of conversation (user + assistant)
  const samplesPerSecond = 24000 * 2; // 24kHz * 16-bit
  for (let i = 0; i < 3; i++) {
    const pcmChunk = Buffer.alloc(samplesPerSecond, Math.floor(Math.random() * 65536));
    writePcm(testSessionId2, pcmChunk);
    await new Promise(resolve => setTimeout(resolve, 100)); // Small delay between chunks
  }
  
  await new Promise(resolve => setTimeout(resolve, 500));
  
  const result2 = await stopRecording(testSessionId2);
  console.log('Result with realistic data:', result2 ? `✅ File created (${fs.statSync(result2.outPath).size} bytes)` : '❌ File too small, skipped');
  
  if (result2) {
    console.log('📄 File details:');
    console.log('  Path:', result2.outPath);
    console.log('  Size:', fs.statSync(result2.outPath).size, 'bytes');
    cleanupSessionTemp(testSessionId2);
  }
  
} catch (e) {
  console.error('❌ Test failed:', e.message);
  console.error(e.stack);
}

console.log('\n💡 Diagnosis Tips:');
console.log('1. If minimal data test fails: Recording service is working correctly (small files are filtered out)');
console.log('2. If realistic data test fails: There may be an ffmpeg or filesystem issue');
console.log('3. If both tests pass: The issue is that not enough audio is being written during real sessions');
console.log('4. Check that both user audio (from ACS) AND assistant audio (from Azure Voice Live) are being written');
