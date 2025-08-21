import Sessions from './src/models/ContactModel.js';
import mongoose from 'mongoose';
import dotenv from 'dotenv';

dotenv.config();

mongoose.connect(process.env.MONGO_URL || process.env.MONGO_URI || 'mongodb://localhost:27017/voice-agent-db')
  .then(async () => {
    console.log('Connected to MongoDB');
    
    // Get recent sessions
    const sessions = await Sessions.find({}).sort({startedAt: -1}).limit(10);
    console.log(`\nFound ${sessions.length} sessions:`);
    
    sessions.forEach((s, i) => {
      console.log(`\n${i+1}. Session ${s.sessionId}:`);
      console.log(`   Status: ${s.status}`);
      console.log(`   Channel: ${s.channel}`);
      console.log(`   Started: ${s.startedAt}`);
      console.log(`   Messages: ${s.messagesCount || 0}`);
      console.log(`   Transcript segments: ${s.transcriptSegments ? s.transcriptSegments.length : 0}`);
      
      if (s.transcriptSegments && s.transcriptSegments.length > 0) {
        console.log(`   Recent transcript:`, s.transcriptSegments.slice(-2).map(t => 
          `${t.speaker}: ${t.content?.slice(0, 50)}...`).join(', '));
      }
    });
    
    process.exit(0);
  })
  .catch(e => {
    console.error('MongoDB error:', e);
    process.exit(1);
  });
