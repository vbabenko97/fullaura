/* Photos for a try-on are redrawn in the browser as upright JPEGs: decoding applies the camera's
 * rotation, and the redrawn file carries no metadata such as location. */
export const ACCEPTED_PHOTO_TYPES = ["image/jpeg", "image/png", "image/webp"];
const MAX_SIDE = 2048;
const MAX_INPUT_BYTES = 30 * 1024 * 1024;
const MAX_OUTPUT_BYTES = 10 * 1024 * 1024;

export const UNSUPPORTED_PHOTO =
  "This photo format cannot be used here. Save it as a JPEG, PNG or WebP and choose it again. " +
  "On an iPhone, Settings > Camera > Formats > Most Compatible takes JPEG photos.";

export class PhotoError extends Error {}

export async function normalizePhoto(file: File): Promise<Blob> {
  if (!ACCEPTED_PHOTO_TYPES.includes(file.type)) throw new PhotoError(UNSUPPORTED_PHOTO);
  if (file.size === 0 || file.size > MAX_INPUT_BYTES) {
    throw new PhotoError("Choose a photo between 1 byte and 30 MB.");
  }
  let bitmap: ImageBitmap;
  try {
    bitmap = await createImageBitmap(file, { imageOrientation: "from-image" });
  } catch {
    throw new PhotoError(UNSUPPORTED_PHOTO);
  }
  try {
    const scale = Math.min(1, MAX_SIDE / Math.max(bitmap.width, bitmap.height));
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(bitmap.width * scale));
    canvas.height = Math.max(1, Math.round(bitmap.height * scale));
    const context = canvas.getContext("2d");
    if (!context) throw new PhotoError("This browser cannot prepare photos. Try another browser.");
    context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    const blob = await new Promise<Blob | null>((resolve) =>
      canvas.toBlob(resolve, "image/jpeg", 0.9),
    );
    if (!blob || blob.size > MAX_OUTPUT_BYTES) {
      throw new PhotoError("This photo could not be prepared. Try a smaller photo.");
    }
    return blob;
  } finally {
    bitmap.close();
  }
}
