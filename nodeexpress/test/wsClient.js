import WebSocket from 'ws';

const ws = new WebSocket('ws://localhost:8080/ws');

ws.on('open', () => {
  console.log('wsClient: connected to /ws');
  // send a small JSON frame similar to ACS AudioData
  ws.send(JSON.stringify({ kind: 'AudioData', audioData: { data: 'SGVsbG8=' } }));
  setTimeout(() => {
    console.log('wsClient: closing');
    ws.close();
    process.exit(0);
  }, 2000);
});

ws.on('message', (msg) => {
  console.log('wsClient: message', msg.toString());
});

ws.on('error', (err) => {
  console.error('wsClient: error', err);
  process.exit(1);
});
