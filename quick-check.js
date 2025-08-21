import { MongoClient } from 'mongodb';
import dotenv from 'dotenv';
dotenv.config();

async function quickCheck() {
    console.log('🔍 Quick MongoDB Session Check');
    
    const client = new MongoClient(process.env.MONGO_URL);
    
    try {
        await client.connect();
        const db = client.db('dashboard');
        const sessions = db.collection('sessions');
        
        // Get latest session
        const latestSession = await sessions.findOne({}, { sort: { createdAt: -1 } });
        
        if (latestSession) {
            console.log(`\n📋 Latest Session: ${latestSession.sessionId}`);
            console.log(`📅 Created: ${latestSession.createdAt}`);
            console.log(`📊 Status: ${latestSession.status}`);
            console.log(`📞 Channel: ${latestSession.channel}`);
            console.log(`📱 Caller ID: ${latestSession.callerId || 'not set'}`);
            if (latestSession.recording) {
                console.log(`🎬 Recording URL: ${latestSession.recording}`);
                console.log(`📊 Recording Size: ${latestSession.recordingSize} bytes`);
            }
            console.log(`💬 Messages: ${latestSession.messageCount || 0}`);
            
            if (latestSession.status === 'completed') {
                console.log('\n🎉 SUCCESS! Recording pipeline is working!');
            } else {
                console.log(`\n⚠️  Status still: ${latestSession.status}`);
            }
        } else {
            console.log('❌ No sessions found');
        }
        
    } catch (error) {
        console.error('Error:', error);
    } finally {
        await client.close();
    }
}

quickCheck().catch(console.error);
