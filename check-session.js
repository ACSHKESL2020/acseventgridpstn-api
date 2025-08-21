import Contact from './src/models/ContactModel.js';

async function checkLatestSession() {
    try {
        const latestSession = await Contact.findOne().sort({ createdAt: -1 });
        console.log('Latest session:', JSON.stringify(latestSession, null, 2));
        console.log('CallerId:', latestSession?.callerId || 'NOT SET');
    } catch (error) {
        console.error('Error checking session:', error);
    } finally {
        process.exit(0);
    }
}

checkLatestSession();
