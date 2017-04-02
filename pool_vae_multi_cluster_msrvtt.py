#-*- coding: utf-8 -*-
import tensorflow as tf
import pandas as pd
import numpy as np
import os, h5py, sys, argparse
import pdb
import time
import json
from collections import defaultdict
#from keras.preprocessing import sequence
from cocoeval import COCOScorer
import unicodedata
#from tensorflow.python.tools.inspect_checkpoint import print_tensors_in_checkpoint_file
from modules.variational_autoencoder_cluster import VAE
from utils.model_ops_cluster_msrvtt import *
from utils.record_helper import read_and_decode

#### custom parameters #####
model_path = '/data11/shenxu/msrvtt_models/pool_vae_multi/'
#### custom parameters #####

class Video_Caption_Generator():
    def __init__(self, dim_image, n_words, dim_hidden, batch_size, n_caption_steps,
        n_video_steps, drop_out_rate, bias_init_vector=None):
        self.dim_image = dim_image
        self.n_words = n_words
        self.dim_hidden = dim_hidden
        self.batch_size = batch_size
        self.n_caption_steps = n_caption_steps
        self.drop_out_rate = drop_out_rate
        self.n_video_steps = n_video_steps

        with tf.device("/cpu:0"):
            self.Wemb = tf.Variable(tf.random_uniform([n_words, dim_hidden], -0.1, 0.1), name='Wemb')

        # encoding LSTM for sentence
        self.lstm2 = tf.nn.rnn_cell.LSTMCell(self.dim_hidden, use_peepholes=True, state_is_tuple=True)
        # decoding LSTM for sentence
        self.lstm3 = tf.nn.rnn_cell.LSTMCell(self.dim_hidden, use_peepholes=True, state_is_tuple=True)
        # decoding LSTM for video
        self.lstm4 = tf.nn.rnn_cell.LSTMCell(self.dim_hidden, use_peepholes=True, state_is_tuple=True)

        self.lstm2_dropout = tf.nn.rnn_cell.DropoutWrapper(self.lstm2,output_keep_prob=1 - self.drop_out_rate)
        self.lstm3_dropout = tf.nn.rnn_cell.DropoutWrapper(self.lstm3,output_keep_prob=1 - self.drop_out_rate)
        self.lstm4_dropout = tf.nn.rnn_cell.DropoutWrapper(self.lstm4,output_keep_prob=1 - self.drop_out_rate)

        self.vae = VAE(self.dim_hidden * 2, self.dim_hidden)

        self.encode_image_W = tf.Variable(tf.random_uniform([dim_image, dim_hidden], -0.1, 0.1),name='encode_image_W')
        self.encode_image_b = tf.Variable(tf.zeros([dim_hidden]), name='encode_image_b')
        self.decode_image_W = tf.Variable(tf.random_uniform([dim_hidden, dim_image], -0.1, 0.1, name='decode_image_W'))
        self.decode_image_b = tf.Variable(tf.random_uniform([dim_image]), name='decode_image_b')

        self.embed_word_W = tf.Variable(tf.random_uniform([dim_hidden, n_words], -0.1,0.1), name='embed_word_W')
        if bias_init_vector is not None:
            self.embed_word_b = tf.Variable(bias_init_vector.astype(np.float32), name='embed_word_b')
        else:
            self.embed_word_b = tf.Variable(tf.zeros([n_words]), name='embed_word_b')

    def build_model(self, video, video_mask, caption, caption_1, caption_2, caption_3, caption_4, caption_5,
        caption_mask, drop_sent='keep', drop_video='keep', caption_weight=1., video_weight=1., latent_weight=0.01):
        caption_mask = tf.cast(caption_mask, tf.float32)
        video_mask = tf.cast(video_mask, tf.float32)
        # for decoding
        video_flat = tf.reshape(video, [-1, self.dim_image]) # (b x nv) x d
        image_emb = tf.nn.xw_plus_b( video_flat, self.encode_image_W, self.encode_image_b) # (b x nv) x h
        image_emb = tf.reshape(image_emb, [self.batch_size, self.n_video_steps, self.dim_hidden]) # b x nv x h
        image_emb = tf.transpose(image_emb, [1,0,2]) # n x b x h

        assert drop_sent in ['totally', 'random', 'keep']
        assert drop_video in ['totally', 'random', 'keep']
        c_init = tf.zeros([self.batch_size, self.dim_hidden]) # b x h
        m_init = tf.zeros([self.batch_size, self.dim_hidden]) # b x h
        state2_1 = (c_init, m_init) # 2 x b x h
        state2_2 = (c_init, m_init) # 2 x b x h
        state2_3 = (c_init, m_init) # 2 x b x h
        state2_4 = (c_init, m_init) # 2 x b x h
        state2_5 = (c_init, m_init) # 2 x b x h

        ######## Encoding Stage #########
        # encoding video
        # mean pooling
        embed_video = tf.reduce_mean(video, 1) # b x d_im
        # embedding into (-1, 1) range
        output1 = tf.nn.tanh(tf.nn.xw_plus_b(embed_video, self.encode_image_W, self.encode_image_b)) # b x h
        # encoding sentence
        with tf.variable_scope("model") as scope:
            for i in xrange(self.n_caption_steps):
                if i > 0: scope.reuse_variables()
                with tf.variable_scope("LSTM2"):
                    with tf.device("/cpu:0"):
                        current_embed_1 = tf.nn.embedding_lookup(self.Wemb, caption_1[:,i]) # b x h
                        current_embed_2 = tf.nn.embedding_lookup(self.Wemb, caption_2[:,i]) # b x h
                        current_embed_3 = tf.nn.embedding_lookup(self.Wemb, caption_3[:,i]) # b x h
                        current_embed_4 = tf.nn.embedding_lookup(self.Wemb, caption_4[:,i]) # b x h
                        current_embed_5 = tf.nn.embedding_lookup(self.Wemb, caption_5[:,i]) # b x h
                    output2_1, state2_1 = self.lstm2_dropout(current_embed_1, state2_1) # b x h
                    tf.get_variable_scope().reuse_variables()
                    output2_2, state2_2 = self.lstm2_dropout(current_embed_2, state2_2) # b x h
                    output2_3, state2_3 = self.lstm2_dropout(current_embed_3, state2_3) # b x h
                    output2_4, state2_4 = self.lstm2_dropout(current_embed_4, state2_4) # b x h
                    output2_5, state2_5 = self.lstm2_dropout(current_embed_5, state2_5) # b x h
        output2 = tf.constant(0.2) * output2_1 + tf.constant(0.2) * output2_2 + tf.constant(0.2) * output2_3 + \
            tf.constant(0.2) * output2_4 + tf.constant(0.2) * output2_5
        ######## Encoding Stage #########

        ######## Dropout Stage #########
        if drop_sent == 'totally':
            output2 = tf.constant(0) * output2
            output2 = tf.stop_gradient(output2)
        elif drop_sent == 'random':
            coeff = tf.floor(tf.random_uniform([1], 0, 1) + 0.5)
            output2 = coeff * output2
        if drop_video == 'totally':
            output1 = tf.constant(0) * output1
            output1 = tf.stop_gradient(output1)
        elif drop_video == 'random':
            coeff = tf.floor(tf.random_uniform([1], 0, 1) + 0.5)
            output1 = coeff * output1
        ######## Dropout Stage #########

        ######## Semantic Learning Stage ########
        input_state = tf.concat(1, [output1, output2]) # b x (2 * h)
        loss_latent, output_semantic = self.vae(input_state)
        ######## Semantic Learning Stage ########

        ######## Decoding Stage ##########
        state3 = (c_init, m_init) # 2 x b x h
        state4 = (c_init, m_init) # 2 x b x h
        current_embed = tf.zeros([self.batch_size, self.dim_hidden]) # b x h
        video_prev = tf.zeros([self.batch_size, self.dim_hidden])

        loss_caption = 0.0
        loss_video = 0.0

        ## decoding sentence without attention
        with tf.variable_scope("model") as scope:
            with tf.variable_scope("LSTM3"):
                _, state3 = self.lstm3_dropout(output_semantic, state3) # b x h
            for i in xrange(n_caption_steps):
                scope.reuse_variables()
                with tf.variable_scope("LSTM3"):
                    output3, state3 = self.lstm3_dropout(current_embed, state3) # b x h
                labels = tf.expand_dims(caption[:,i], 1) # b x 1
                indices = tf.expand_dims(tf.range(0, self.batch_size, 1), 1) # b x 1
                concated = tf.concat(1, [indices, labels]) # b x 2
                onehot_labels = tf.sparse_to_dense(concated,
                    tf.pack([self.batch_size, self.n_words]), 1.0, 0.0) # b x w
                with tf.device("/cpu:0"):
                    current_embed = tf.nn.embedding_lookup(self.Wemb, caption[:,i])
                logit_words = tf.nn.xw_plus_b(output3, self.embed_word_W, self.embed_word_b) # b x w
                cross_entropy = tf.nn.softmax_cross_entropy_with_logits(logits = logit_words,
                    labels = onehot_labels) # b x 1
                cross_entropy = cross_entropy * caption_mask[:,i] # b x 1
                loss_caption += tf.reduce_sum(cross_entropy) # 1

        ## decoding video without attention
        with tf.variable_scope("model") as scope:
            ## TODO: add attention for video decoding
            ## write into memory first
            with tf.variable_scope("LSTM4"):
                _, state4 = self.lstm4_dropout(output_semantic, state4)
            for i in xrange(self.n_video_steps):
                scope.reuse_variables()
                with tf.variable_scope("LSTM4"):
                    output4, state4 = self.lstm4_dropout(video_prev, state4)
                decode_image = tf.nn.xw_plus_b(output4, self.decode_image_W, self.decode_image_b) # b x d_im
                video_prev = image_emb[i, :, :] # b x h
                euclid_loss = tf.reduce_sum(tf.square(tf.sub(decode_image, video[:,i,:])),
                    1, keep_dims=True) # b x 1
                euclid_loss = euclid_loss * video_mask[:, i] # b x 1
                loss_video += tf.reduce_sum(euclid_loss) # 1

        loss_caption = loss_caption / tf.reduce_sum(caption_mask)
        loss_video = loss_video / tf.reduce_sum(video_mask)

        loss = caption_weight * loss_caption + video_weight * loss_video + latent_weight * loss_latent
        return loss, loss_caption, loss_latent, loss_video, output_semantic


    def build_sent_generator(self, video):
        ####### Encoding Video ##########
        # encoding video
        embed_video = tf.reduce_mean(video, 1) # b x d_im
        # embedding into (0, 1) range
        output1 = tf.nn.tanh(tf.nn.xw_plus_b(embed_video, self.encode_image_W, self.encode_image_b)) # b x h
        ####### Encoding Video ##########

        ####### Semantic Mapping ########
        output2 = tf.zeros([self.batch_size, self.dim_hidden]) # b x h
        input_state = tf.concat(1, [output1, output2]) # b x h, b x h
        _, output_semantic = self.vae(input_state)
        ####### Semantic Mapping ########

        ####### Decoding ########
        c_init = tf.zeros([self.batch_size, self.dim_hidden]) # b x h
        m_init = tf.zeros([self.batch_size, self.dim_hidden]) # b x h
        state3 = (c_init, m_init) # n x 2 x h
        current_embed = tf.zeros([self.batch_size, self.dim_hidden]) # b x h

        generated_words = []

        with tf.variable_scope("model") as scope:
            scope.reuse_variables()
            with tf.variable_scope("LSTM3"):
                _, state3 = self.lstm3_dropout(output_semantic, state3) # b x h
            for i in range(self.n_caption_steps):
                with tf.variable_scope("LSTM3") as vs:
                    output3, state3 = self.lstm3(current_embed, state3 ) # b x h
                    lstm3_variables = [v for v in tf.all_variables() if v.name.startswith(vs.name)]
                logit_words = tf.nn.xw_plus_b(output3, self.embed_word_W, self.embed_word_b) # b x w
                max_prob_index = tf.argmax(logit_words, 1) # b
                generated_words.append(max_prob_index) # b
                with tf.device("/cpu:0"):
                    current_embed = tf.nn.embedding_lookup(self.Wemb, max_prob_index)
        ####### Decoding ########

        generated_words = tf.transpose(tf.pack(generated_words)) # n_caption_step x 1
        return generated_words, lstm3_variables

    def build_video_generator(self, sent_1, sent_2, sent_3, sent_4, sent_5):
        c_init = tf.zeros([self.batch_size, self.dim_hidden]) # b x h
        m_init = tf.zeros([self.batch_size, self.dim_hidden]) # b x h
        state2_1 = (c_init, m_init) # 2 x b x h
        state2_2 = (c_init, m_init) # 2 x b x h
        state2_3 = (c_init, m_init) # 2 x b x h
        state2_4 = (c_init, m_init) # 2 x b x h
        state2_5 = (c_init, m_init) # 2 x b x h
        ####### Encoding Sentence ##########
        with tf.variable_scope("model") as scope:
            scope.reuse_variables()
            for i in xrange(self.n_caption_steps):
                with tf.variable_scope("LSTM2"):
                    with tf.device("/cpu:0"):
                        current_embed_1 = tf.nn.embedding_lookup(self.Wemb, sent_1[:,i]) # b x h
                        current_embed_2 = tf.nn.embedding_lookup(self.Wemb, sent_2[:,i]) # b x h
                        current_embed_3 = tf.nn.embedding_lookup(self.Wemb, sent_3[:,i]) # b x h
                        current_embed_4 = tf.nn.embedding_lookup(self.Wemb, sent_4[:,i]) # b x h
                        current_embed_5 = tf.nn.embedding_lookup(self.Wemb, sent_5[:,i]) # b x h
                    output2_1, state2_1 = self.lstm2_dropout(current_embed_1, state2_1) # b x h
                    output2_2, state2_2 = self.lstm2_dropout(current_embed_2, state2_2) # b x h
                    output2_3, state2_3 = self.lstm2_dropout(current_embed_3, state2_3) # b x h
                    output2_4, state2_4 = self.lstm2_dropout(current_embed_4, state2_4) # b x h
                    output2_5, state2_5 = self.lstm2_dropout(current_embed_5, state2_5) # b x h
        output2 = tf.constant(0.2) * output2_1 + tf.constant(0.2) * output2_2 + tf.constant(0.2) * output2_3 + \
            tf.constant(0.2) * output2_4 + tf.constant(0.2) * output2_5
        ####### Encoding Sentence ##########

        ####### Semantic Mapping ########
        output1 = tf.zeros([self.batch_size, self.dim_hidden]) # b x h
        input_state = tf.concat(1, [output1, output2]) # b x (2 * h)
        _, output_semantic = self.vae(input_state)
        ####### Semantic Mapping ########

        ####### Decoding ########
        state4 = (c_init, m_init) # n x 2 x h
        image_emb = tf.zeros([self.batch_size, self.dim_hidden])

        generated_images = []

        with tf.variable_scope("model") as scope:
            scope.reuse_variables()
            with tf.variable_scope("LSTM4"):
                _, state4 = self.lstm4(output_semantic, state4)
            for i in range(self.n_video_steps):
                with tf.variable_scope("LSTM4") as vs:
                    output4, state4 = self.lstm4(image_emb, state4) # b x h
                    lstm4_variables = [v for v in tf.all_variables() if v.name.startswith(vs.name)]

                image_prev = tf.nn.xw_plus_b(output4, self.decode_image_W, self.decode_image_b)
                image_emb = tf.nn.xw_plus_b(image_prev, self.encode_image_W, self.encode_image_b)
                generated_images.append(image_prev) # b x d_im
        ####### Decoding ########

        generated_images = tf.transpose(tf.pack(generated_images), [1, 0, 2]) # b x n_video_step x d_im
        return generated_images, lstm4_variables

def train():
    assert os.path.isdir(home_folder)
    assert os.path.isfile(video_data_path_train)
    assert os.path.isfile(video_data_path_val)
    assert os.path.isdir(model_path)
    print 'load meta data...'
    wordtoix = np.load(home_folder + 'data0/msrvtt_wordtoix.npy').tolist()
    print 'build model and session...'
    # shared parameters on the GPU
    with tf.device("/gpu:0"):
        model = Video_Caption_Generator(
                dim_image=dim_image,
                n_words=len(wordtoix),
                dim_hidden=dim_hidden,
                batch_size=batch_size,
                n_caption_steps=n_caption_steps,
                n_video_steps=n_video_steps,
                drop_out_rate = 0.5,
                bias_init_vector=None)
    tStart_total = time.time()
    n_epoch_steps = int(n_train_samples / batch_size)
    n_steps = n_epochs * n_epoch_steps
    # preprocess on the CPU
    with tf.device('/cpu:0'):
        train_data, train_encode_data, _, _, train_video_label, train_caption_label, train_caption_id, train_caption_id_1, \
            train_caption_id_2, train_caption_id_3, train_caption_id_4, train_caption_id_5 = read_and_decode(video_data_path_train)
        val_data, val_encode_data, val_fname, val_title, val_video_label, val_caption_label, val_caption_id, val_caption_id_1, \
            val_caption_id_2, val_caption_id_3, val_caption_id_4, val_caption_id_5 = read_and_decode(video_data_path_val)
        # random batches
        train_data, train_encode_data, train_video_label, train_caption_label, train_caption_id, train_caption_id_1, \
            train_caption_id_2, train_caption_id_3, train_caption_id_4, train_caption_id_5 = \
            tf.train.shuffle_batch([train_data, train_encode_data, train_video_label, train_caption_label, train_caption_id, train_caption_id_1, \
                train_caption_id_2, train_caption_id_3, train_caption_id_4, train_caption_id_5], \
                batch_size=batch_size, num_threads=num_threads, capacity=prefetch, min_after_dequeue=min_queue_examples)
        val_data, val_video_label, val_fname, val_caption_label, val_caption_id_1, val_caption_id_2, val_caption_id_3, \
            val_caption_id_4, val_caption_id_5 = \
            tf.train.batch([val_data, val_video_label, val_fname, val_caption_label, val_caption_id_1, val_caption_id_2, \
                val_caption_id_3, val_caption_id_4, val_caption_id_5], \
                batch_size=batch_size, num_threads=2, capacity=3* batch_size)
    # graph on the GPU
    with tf.device("/gpu:0"):
        tf_loss, tf_loss_cap, tf_loss_lat, tf_loss_vid, tf_z = model.build_model(train_data, train_video_label, \
            train_caption_id, train_caption_id_1, train_caption_id_2, train_caption_id_3, train_caption_id_4, \
            train_caption_id_5, train_caption_label)
        val_caption_tf, val_lstm3_variables_tf = model.build_sent_generator(val_data)
        val_video_tf, val_lstm4_variables_tf = model.build_video_generator(val_caption_id_1, val_caption_id_2, val_caption_id_3, val_caption_id_4, \
            val_caption_id_5)
    sess = tf.InteractiveSession(config=tf.ConfigProto(allow_soft_placement=True, log_device_placement=False))
    # check for model file
    with tf.device("/cpu:0"):
        saver = tf.train.Saver(max_to_keep=100)
    ckpt = tf.train.get_checkpoint_state(model_path)
    global_step = 0
    if ckpt and tf.train.checkpoint_exists(ckpt.model_checkpoint_path):
        print("Reading model parameters from %s" % ckpt.model_checkpoint_path)
        saver.restore(sess, ckpt.model_checkpoint_path)
        global_step = get_model_step(ckpt.model_checkpoint_path)
        print 'global_step:', global_step
#        print_tensors_in_checkpoint_file(ckpt.model_checkpoint_path, "", True)
    else:
        print("Created model with fresh parameters.")
        sess.run(tf.initialize_all_variables())
    temp = set(tf.all_variables())
    # train on the GPU
    with tf.device("/gpu:0"):
#        train_op = tf.train.AdamOptimizer(learning_rate).minimize(tf_loss)
        ## initialize variables added for optimizer
        optimizer = tf.train.AdamOptimizer(learning_rate)
        gvs = optimizer.compute_gradients(tf_loss)
        # when variable is not related to the loss, grad returned as None
        clip_gvs = [(tf.clip_by_norm(grad, clip_norm), var) for grad, var in gvs if grad is not None]
        train_op = optimizer.apply_gradients(gvs)

    sess.run(tf.initialize_variables(set(tf.all_variables()) - temp))
    # initialize epoch variable in queue reader
    sess.run(tf.initialize_local_variables())
    loss_epoch = 0
    coord = tf.train.Coordinator()
    threads = tf.train.start_queue_runners(sess=sess, coord=coord)

    # write graph architecture to file
#    summary_writer = tf.summary.FileWriter(model_path + 'summary', sess.graph)

    epoch = global_step
    for step in xrange(1, n_steps+1):
        tStart = time.time()
#        _, loss_val, loss_cap, loss_lat, loss_vid = sess.run([train_op, tf_loss, tf_loss_cap, tf_loss_lat, tf_loss_vid])
        _, loss_val, loss_cap, loss_lat, loss_vid, z = sess.run([train_op, tf_loss, tf_loss_cap, tf_loss_lat, tf_loss_vid, tf_z])
        tStop = time.time()
        print "step:", step, " Loss:", loss_val, "loss_cap:", loss_cap, "loss_lat:", loss_lat, "loss_vid:", loss_vid
        print "Time Cost:", round(tStop - tStart, 2), "s"
        loss_epoch += loss_val

        if step % n_epoch_steps == 0:
            epoch += 1
            loss_epoch /= n_epoch_steps
            with tf.device("/cpu:0"):
                saver.save(sess, os.path.join(model_path, 'model'), global_step=epoch)
            print 'z:', z[0, :10]
            print 'epoch:', epoch, 'loss:', loss_epoch, "loss_cap:", loss_cap, "loss_lat:",loss_lat, "loss_vid:", loss_vid
            loss_epoch = 0
            ######### test sentence generation ##########
            ixtoword = pd.Series(np.load(home_folder + 'data0/msrvtt_ixtoword.npy').tolist())
            n_val_steps = int(n_val_samples / batch_size)
            [pred_sent, gt_sent, id_list, gt_dict, pred_dict] = testing_all(sess, 1, ixtoword, val_caption_tf, val_fname)
            for key in pred_dict.keys():
                for ele in gt_dict[key]:
                    print "GT:  " + ele['caption']
                print "PD:  " + pred_dict[key][0]['caption']
                print '-------'
            [pred_sent, gt_sent, id_list, gt_dict, pred_dict] = testing_all(sess, n_val_steps, ixtoword, val_caption_tf, val_fname)
            scorer = COCOScorer()
            total_score = scorer.score(gt_dict, pred_dict, id_list)
            ######### test video generation #############
            mse = test_all_videos(sess, n_val_steps, val_data, val_video_tf)
            sys.stdout.flush()

        sys.stdout.flush()

    coord.request_stop()
    coord.join(threads)
    print "Finally, saving the model ..."
    with tf.device("/cpu:0"):
        saver.save(sess, os.path.join(model_path, 'model'), global_step=n_epochs)
    tStop_total = time.time()
    print "Total Time Cost:", round(tStop_total - tStart_total,2), "s"
    sess.close()

def test(model_path='models/model-900', video_feat_path=video_feat_path):
    meta_data, train_data, val_data, test_data = get_video_data_jukin(video_data_path_train, video_data_path_val, video_data_path_test)
#    test_data = val_data   # to evaluate on testing data or validation data
    ixtoword = pd.Series(np.load('./data0/msrvtt_ixtoword.npy').tolist())

    model = Video_Caption_Generator(
            dim_image=dim_image,
            n_words=len(ixtoword),
            dim_hidden=dim_hidden,
            batch_size=batch_size,
            n_lstm_steps=n_frame_step,
            drop_out_rate = 0,
            bias_init_vector=None)

    video_tf, video_mask_tf, caption_tf, lstm3_variables_tf = model.build_generator()
    sess = tf.InteractiveSession(config=tf.ConfigProto(allow_soft_placement=True))

    with tf.device("/cpu:0"):
        saver = tf.train.Saver()
        saver.restore(sess, model_path)

    for ind, row in enumerate(lstm3_variables_tf):
        if ind % 4 == 0:
                assign_op = row.assign(tf.mul(row,1-0.5))
                sess.run(assign_op)

    [pred_sent, gt_sent, id_list, gt_dict, pred_dict] = testing_all(sess, test_data, ixtoword,video_tf, video_mask_tf, caption_tf)
    #np.savez('Att_result/'+model_path.split('/')[1],gt = gt_sent,pred=pred_sent)
    scorer = COCOScorer()
    total_score = scorer.score(gt_dict, pred_dict, id_list)
    return total_score

if __name__ == '__main__':
    args = parse_args()
    if args.task == 'train':
        train()
    elif args.task == 'test':
        with tf.device('/gpu:'+str(args.gpu_id)):
            total_score = test(model_path = args.model)
