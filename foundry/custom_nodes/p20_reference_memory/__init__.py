"""Release completed reference encoders before loading the diffusion model.

This node's input dependencies wait for both grounded encodes and source VAE
encoding. ComfyUI's own model manager offloads the finished models; conditioning
and latent values pass through unchanged. No sampler, device or guard replacement.
"""
import comfy.model_management


class P20ReferenceMemoryBarrier:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {'model': ('MODEL',), 'positive': ('CONDITIONING',),
                             'negative': ('CONDITIONING',), 'source_latent': ('LATENT',)}}

    RETURN_TYPES = ('MODEL', 'CONDITIONING', 'CONDITIONING', 'LATENT')
    FUNCTION = 'prepare'
    CATEGORY = 'conditioning/reference'

    def prepare(self, model, positive, negative, source_latent):
        manager = comfy.model_management
        manager.free_memory(1e30, manager.get_torch_device())
        manager.soft_empty_cache()
        print('[p20-reference] Completed encoders offloaded before diffusion sampling', flush=True)
        return model, positive, negative, source_latent


NODE_CLASS_MAPPINGS = {'P20ReferenceMemoryBarrier': P20ReferenceMemoryBarrier}
NODE_DISPLAY_NAME_MAPPINGS = {'P20ReferenceMemoryBarrier': 'Prepare reference for sampling'}
