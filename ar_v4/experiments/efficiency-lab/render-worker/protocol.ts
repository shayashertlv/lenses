import type {Detection} from '../../../references/perfect-temples/src/runtime/detector.ts';
import type {EyewearId, EyewearDefinition} from '../../../references/perfect-temples/src/render/eyewear.ts';
import type {HairMask, HairModelContract, PairIdentity, LiveHairStats, CaptureGeometry} from '../../speed-lab/renderer.ts';
import type {LiveVariant} from '../../hair-live-preview/ownership.ts';
export interface FrameOwner {sessionId: number|string; generation: number; sourceSHA256: string; detectionSHA256: string;}
export interface CompletedFrame {
  owner: FrameOwner; visible: boolean; accepted: ImageData; hair: ImageData;
  stats: LiveHairStats|null; snapshot: CaptureGeometry|null;
  workerCompleteMs: number; outputCopyMs: number;
}
export type RequestPayload = {type:'init'; eyewearId:EyewearId}
  | {type:'prepare'; owner:FrameOwner; rgba:ImageData; detection:Detection; pair:PairIdentity; model:HairModelContract; needsHair:boolean; variant:LiveVariant}
  | {type:'complete'; owner:FrameOwner; mask:HairMask|null}
  | {type:'diagnostic'; owner:FrameOwner; variant:LiveVariant};
export type WorkerRequest = RequestPayload & {requestId:number; session:string};
export type ResponsePayload = {type:'ready'; eyewear:EyewearDefinition; nativeSamples:number}
  | {type:'prepared'; owner:FrameOwner; visible:boolean; workerPrepareMs:number}
  | {type:'completed'; result:CompletedFrame}
  | {type:'diagnostic'; owner:FrameOwner; diagnostic:Record<string,unknown>|null}
  | {type:'error'; message:string};
export type WorkerResponse = ResponsePayload & {requestId:number; session:string};
export function assertOwner(owner:FrameOwner):void {
  if(!owner || !Number.isSafeInteger(owner.generation) || owner.generation<0
    || !(typeof owner.sessionId==='string' && owner.sessionId.length>0 || typeof owner.sessionId==='number' && Number.isSafeInteger(owner.sessionId) && owner.sessionId>=0)
    || !/^[a-f0-9]{64}$/.test(owner.sourceSHA256) || !/^[a-f0-9]{64}$/.test(owner.detectionSHA256))
    throw new Error('Invalid render-worker frame ownership.');
}
export function assertSameOwner(actual:FrameOwner, expected:FrameOwner):void {
  assertOwner(actual);assertOwner(expected);
  if(actual.sessionId!==expected.sessionId || actual.generation!==expected.generation
    || actual.sourceSHA256!==expected.sourceSHA256 || actual.detectionSHA256!==expected.detectionSHA256)
    throw new Error('Render-worker response belongs to another owned frame.');
}
export function assertPixels(image:ImageData):void {
  if(!image || !Number.isSafeInteger(image.width)||!Number.isSafeInteger(image.height) || image.width<=0||image.height<=0
    || image.width>8192||image.height>8192||image.width*image.height>16_777_216 || image.colorSpace!=='srgb'
    || !(image.data instanceof Uint8ClampedArray)||!(image.data.buffer instanceof ArrayBuffer)
    || image.data.byteOffset!==0 || image.data.byteLength!==image.width*image.height*4 || image.data.buffer.byteLength!==image.data.byteLength)
    throw new Error('Render-worker image must have exact independent sRGB storage.');
}
