import fs from 'fs';
import path from 'path';
import { Readable } from 'stream';
import { BlobServiceClient } from '@azure/storage-blob';
import { sha256Stream } from '../utils/checksum.js';

export async function uploadFile(localPath, destBlobName) {
	const connectionString = process.env.AZURE_STORAGE_CONNECTION_STRING;
	const containerName = process.env.AZURE_BLOB_CONTAINER;
	const blobBaseUrl = process.env.AZURE_BLOB_URL; // optional

	if (!connectionString) throw new Error('No AZURE_STORAGE_CONNECTION_STRING configured');
	const bsc = BlobServiceClient.fromConnectionString(connectionString);
	const container = bsc.getContainerClient(containerName);
	await container.createIfNotExists();

	const blockBlobClient = container.getBlockBlobClient(destBlobName);

	try { console.info(`Uploader: starting upload localPath=${localPath} blob=${destBlobName} container=${containerName}`); } catch (e) {}
	const sha = await sha256Stream(fs.createReadStream(localPath));
	const stat = fs.statSync(localPath);

	// upload
	const uploadResp = await blockBlobClient.uploadStream(fs.createReadStream(localPath), 4 * 1024 * 1024, 5);
	const etag = uploadResp.etag;
	const url = blobBaseUrl ? `${blobBaseUrl}/${destBlobName}` : blockBlobClient.url;

	try { console.info(`Uploader: finished upload blob=${destBlobName} url=${url} size=${stat.size}`); } catch (e) {}
	try { console.debug(`Uploader: uploadResp=${JSON.stringify(uploadResp)}`); } catch (e) {}

	return {
		url,
		etag,
		sizeBytes: stat.size,
		sha256: sha,
		blobName: destBlobName,
	};
}
